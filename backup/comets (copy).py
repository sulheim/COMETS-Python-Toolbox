# imports
import re
import math
import subprocess as sp
import pandas as pd
import os
import cobra
from cobra import io


class CorruptLine(Exception):
    pass


class OutOfGrid(Exception):
    pass


class UnallocatedMetabolite(Exception):
    pass


def isfloat(value):
    try:
        float(value)
        return True
    except ValueError:
        return False


def read_file(filename):
    f = open(filename, 'r')
    f_lines = f.read()
    f.close()
    return f_lines


def readlines_file(filename):
    f = open(filename, 'r')
    f_lines = f.readlines()
    f.close()
    return f_lines


'''
Class dealing with COMETS layouts
'''


class layout:
    def __init__(self, input_obj=None):

        # define an empty layout that can be filled later
        self.models = []
        self.grid = []
        self.media = pd.DataFrame(columns=['metabolite',
                                           'init_amount',
                                           'diff_c',
                                           'g_static',
                                           'g_static_val',
                                           'g_refresh'])
        self.global_diff = None
        self.local_refresh = []
        self.local_static = []
        self.initial_pop_type = None
        self.initial_pop = []
        
        if isinstance(input_obj, str):
            # .. load file
            filedata_string = read_file(input_obj)
            f_lines = readlines_file(input_obj)
            end_blocks = []
            for i in range(0, len(f_lines)):
                if '//\n' in f_lines[i]:
                    end_blocks.append(i)

            # '''----------- MODELS ----------------------------------------'''
            '''
            Models can be specified in layout as either comets format models
            or .xml format (sbml cobra compliant)
            '''            
            try:
                self.models = f_lines[0].split()[1:]
                if len(self.models) == 0:
                    raise CorruptLine
            except CorruptLine:
                print('\n CorruptLine ERROR \n' +
                      'No models specified in provided layout file')

            # read specified models and then write them in comets format
            for i in self.models:

                # define type of input model
                if i in vars() and isinstance(i, cobra.Model):
                    input_type = 'cobra'
                    ext = 'current'
                elif isinstance(i, str):
                    with open(i) as f:
                        ext = f.readline().split()[0]
                        if ext == '<?xml':
                            input_type = 'cobra'
                        else:
                            input_type = 'comets'
                else:
                    print('WARNING:\nCannot find model ' + i + 'anywhere!')
                
                vmax_flag = False
                km_flag = False
                hill_flag = False
                
                if input_type == 'cobra':
                    '''
                    it is a cobra type model; read it and parse it
                    '''
                    if ext == 'current':
                        curr_m = i
                    else:
                        curr_m = io.read_sbml_model(i)

                    model_id = curr_m.id
                    # reactions and their features
                    reaction_list = curr_m.reactions
                    reactions = pd.DataFrame(columns=['REACTION_NAMES', 'ID',
                                                      'LB', 'UB', 'EXCH'])
                    reactions['REACTION_NAMES'] = [str(x).split(':')[0] for
                                                   x in reaction_list]
                    reactions['ID'] = [k for k in
                                       range(1, len(reaction_list)+1)]
                    reactions['LB'] = [x.lower_bound for x in reaction_list]
                    reactions['UB'] = [x.upper_bound for x in reaction_list]

                    reactions['EXCH'] = [True if (len(k.metabolites) == 1) &
                                         (list(k.metabolites.
                                               values())[0] == (-1))
                                         else False for k in reaction_list]

                    reactions['Vmax'] = [k.Vmax
                                         if hasattr(k, 'Vmax')
                                         else float('NaN')
                                         for k in reaction_list]
                    if reactions.Vmax.isnull().all():
                        vmax_flag = True
                        
                    reactions['Km'] = [k.Km
                                       if hasattr(k, 'Km')
                                       else float('NaN')
                                       for k in reaction_list]
                    if reactions.Km.isnull().all():
                        km_flag = True
                        
                    reactions['Hill'] = [k.Hill
                                         if hasattr(k, 'Hill')
                                         else float('NaN')
                                         for k in reaction_list]
                    if reactions.Hill.isnull().all():
                        hill_flag = True

                    if vmax_flag:
                        if hasattr(curr_m, 'default_vmax'):
                            default_vmax = curr_m.default.vmax
                        else:
                            default_vmax = 10

                    if km_flag:
                        if hasattr(curr_m, 'default_km'):
                            default_km = curr_m.default.km
                        else:
                            default_km = 1

                    if hill_flag:
                        if hasattr(curr_m, 'default_hill'):
                            default_hill = curr_m.default.hill
                        else:
                            default_hill = 1
                            
                    # Metabolites
                    metabolites = pd.DataFrame(columns=['METABOLITE_NAMES'])
                    metabolite_list = curr_m.metabolites
                    metabolites['METABOLITE_NAMES'] = [str(x) for
                                                       x in metabolite_list]

                    # S matrix
                    smat = pd.DataFrame(columns=['metabolite',
                                                 'rxn',
                                                 's_coef'])
                    for index, row in reactions.iterrows():
                        rxn = curr_m.reactions.get_by_id(
                            row['REACTION_NAMES'])
                        rxn_num = row['ID']
                        rxn_mets = [1+list(metabolites[
                            'METABOLITE_NAMES']).index(
                            x.id) for x in rxn.metabolites]
                        met_s_coefs = list(rxn.metabolites.values())

                        cdf = pd.DataFrame({'metabolite': rxn_mets,
                                            'rxn': [rxn_num]*len(rxn_mets),
                                            's_coef': met_s_coefs})
                        cdf = cdf.sort_values('metabolite')
                        smat = pd.concat([smat, cdf])

                    smat = smat.sort_values(by=['metabolite', 'rxn'])

                    # The rest of stuff
                    if hasattr(curr_m, 'default_bounds'):
                        default_bounds = curr_m.default_bounds
                    else:
                        default_bounds = [0, 1000]

                    obj = [str(x).split(':')[0]
                           for x in reaction_list
                           if x.objective_coefficient != 0][0]
                    objective = int(reactions[reactions.
                                              REACTION_NAMES == obj]['ID'])

                    if hasattr(curr_m, 'comets_optimizer'):
                        optimizer = curr_m.comets_optimizer
                    else:
                        optimizer = 'GUROBI'

                    if hasattr(curr_m, 'comets_obj_style'):
                        obj_style = curr_m.comets_obj_style
                    else:
                        obj_style = 'MAXIMIZE_OBJECTIVE_FLUX'

                elif ext[0] == 'SMATRIX':
                    '''
                    it is a comets type model; read it and parse it
                    '''
                    model_id = os.path.splitext(os.path.basename(i))[0]
                    m_filedata_string = read_file(i)
                    m_f_lines = readlines_file(i)
                    ends = []
                    for k in range(0, len(m_f_lines)):
                        if '//\n' in m_f_lines[k]:
                            ends.append(k)

                    # '''----------- S MATRIX ------------------------------'''
                    lin_smat = re.split('SMATRIX',
                                        m_filedata_string)[0].count('\n')
                    lin_smat_end = next(x for x in ends if x > lin_smat)-2

                    # TODO: fix delimiter here to be robust to tabs, and test
                    smat = pd.read_csv(i, delimiter=r'\s+',
                                       skiprows=lin_smat,
                                       skipfooter=ends[-1] - lin_smat_end,
                                       engine='python',
                                       skipinitialspace=True)
                    smat.columns = ['metabolite', 'rxn', 's_coef']

                    # '''----------- REACTIONS AND BOUNDS-------------------'''
                    lin_rxns = re.split('REACTION_NAMES',
                                        m_filedata_string)[0].count('\n')
                    lin_rxns_end = next(x for x in
                                        ends if x > lin_rxns)-2

                    rxn = pd.read_csv(i, delimiter=r'\s+',
                                      skiprows=lin_rxns,
                                      skipfooter=ends[-1] - lin_rxns_end,
                                      engine='python', skipinitialspace=True)
                    rxn['ID'] = range(1, len(rxn)+1)

                    lin_bnds = re.split('BOUNDS',
                                        m_filedata_string)[0].count('\n')
                    lin_bnds_end = next(x for x in ends if x > lin_bnds)-2

                    bnds = pd.read_csv(i, delimiter=r'\s+',
                                       skiprows=lin_bnds,
                                       skipfooter=ends[-1] - lin_bnds_end,
                                       engine='python', skipinitialspace=True)

                    default_bounds = [int(bnds.columns[1]),
                                      int(bnds.columns[2])]

                    bnds.columns = ['ID', 'LB', 'UB']
                    reactions = pd.merge(rxn, bnds,
                                         left_on='ID', right_on='ID',
                                         how='left')
                    
                    # '''----------- METABOLITES ---------------------------'''
                    lin_mets = re.split('METABOLITE_NAMES',
                                        m_filedata_string)[0].count('\n')
                    lin_mets_end = next(x for x in ends if x > lin_mets)-2

                    metabolites = pd.read_csv(i, delimiter=r'\s+',
                                              skiprows=lin_mets,
                                              skipfooter=ends[-1] -
                                              lin_mets_end,
                                              engine='python',
                                              skipinitialspace=True)

                    # '''----------- EXCHANGE RXNS -------------------------'''
                    lin_exch = re.split('EXCHANGE_REACTIONS',
                                        m_filedata_string)[0].count('\n')+1
                    exch = [int(k) for k in re.findall(r'\S+',
                                                       m_f_lines[lin_exch].
                                                       strip())]

                    reactions['EXCH'] = [True if x in exch else False
                                         for x in reactions['ID']]
                    reactions['EXCH_IND'] = [exch.index(x)+1
                                             if x in exch else 0
                                             for x in reactions['ID']]

                    # '''----------- VMAX VALUES --------------------------'''
                    if 'VMAX_VALUES' in m_filedata_string:
                        vmax_flag = True
                        lin_vmax = re.split('VMAX_VALUES',
                                            m_filedata_string)[0].count('\n')+1
                        lin_vmax_end = next(x for x in ends if x > lin_vmax)-1
                        
                        Vmax = pd.read_csv(i, delimiter=r'\s+',
                                           header=None,
                                           skiprows=lin_vmax,
                                           skipfooter=ends[-1] -
                                           lin_vmax_end,
                                           engine='python',
                                           skipinitialspace=True)
                        Vmax.columns = ['EXCH_IND', 'V_MAX']
                        
                        reactions = pd.merge(reactions, Vmax,
                                             left_on='EXCH_IND',
                                             right_on='EXCH_IND',
                                             how='left')
                        default_vmax = float(m_f_lines[lin_vmax-1].split()[1])

                    # '''----------- VMAX VALUES --------------------------'''
                    if 'KM_VALUES' in m_filedata_string:
                        km_flag = True
                        lin_km = re.split('KM_VALUES',
                                          m_filedata_string)[0].count('\n')+1
                        lin_km_end = next(x for x in ends if x > lin_km)-1

                        Km = pd.read_csv(i, delimiter=r'\s+',
                                         header=None,
                                         skiprows=lin_km,
                                         skipfooter=ends[-1] -
                                         lin_km_end,
                                         engine='python',
                                         skipinitialspace=True)
                        Km.columns = ['EXCH_IND', 'KM']

                        reactions = pd.merge(reactions, Km,
                                             left_on='EXCH_IND',
                                             right_on='EXCH_IND',
                                             how='left')                    
                        default_km = float(m_f_lines[lin_km-1].split()[1])

                    # '''----------- VMAX VALUES --------------------------'''
                    if 'HILL_COEFFICIENTS' in m_filedata_string:
                        hill_flag = True
                        lin_hill = re.split('HILL_COEFFICIENTS',
                                            m_filedata_string)[0].count('\n')+1
                        lin_hill_end = next(x for x in ends if x > lin_hill)-1

                        Hill = pd.read_csv(i, delimiter=r'\s+',
                                           header=None,
                                           skiprows=lin_hill,
                                           skipfooter=ends[-1] -
                                           lin_hill_end,
                                           engine='python',
                                           skipinitialspace=True)
                        Hill.columns = ['EXCH_IND', 'HILL']

                        reactions = pd.merge(reactions, Hill,
                                             left_on='EXCH_IND',
                                             right_on='EXCH_IND',
                                             how='left')
                        default_hill = float(m_f_lines[lin_hill-1].split()[1])

                    # '''----------- OBJECTIVE -----------------------------'''
                    lin_obj = re.split('OBJECTIVE',
                                       m_filedata_string)[0].count('\n')+1
                    objective = int(m_f_lines[lin_obj].strip())

                    # '''----------- OBJECTIVE STYLE -----------------------'''
                    if 'OBJECTIVE_STYLE' in m_filedata_string:
                        lin_obj_st = re.split('OBJECTIVE_STYLE',
                                              m_filedata_string)[0].count(
                                                  '\n')+1
                        obj_style = m_f_lines[lin_obj_st].strip()
                    else:
                        obj_style = 'MAXIMIZE_OBJECTIVE_FLUX'
                        
                    # '''----------- OPTIMIZER -----------------------------'''
                    if 'OPTIMIZER' in m_filedata_string:
                        lin_opt = re.split('OPTIMIZER',
                                           m_filedata_string)[0].count('\n')+1
                        optimizer = m_f_lines[lin_opt].split()[0]
                    else:
                        optimizer = 'GUROBI'
                else:
                    print('Model ' + i + ' format is not recognized, ' +
                          'simulation will fail')

                # format variables for writing comets model
                bnd = reactions.loc[(reactions['LB'] != default_bounds[0]) |
                                    (reactions['UB'] != default_bounds[1]),
                                    ['ID', 'LB', 'UB']].astype(
                                        str).apply(lambda x: '   '.join(x),
                                                   axis=1)                
                bnd = '    ' + bnd.astype(str)

                rxn_n = '    ' + reactions['REACTION_NAMES'].astype(str)

                met_n = '    ' + metabolites.astype(str)
        
                smat = smat.astype(str).apply(lambda x:
                                              '   '.join(x), axis=1)
                smat = '    ' + smat.astype(str)

                exch_r = ' '.join([str(x) for x in
                                   reactions.loc[reactions.EXCH, 'ID']])
                
                # optional fields (vmax,km, hill)
                if vmax_flag:
                    Vmax = reactions.loc[reactions['Vmax'].notnull(),
                                         ['ID', 'Vmax']]
                    Vmax = Vmax.astype(str).apply(lambda x:
                                                  '   '.join(x), axis=1)
                    Vmax = '    ' + Vmax.astype(str)

                if km_flag:
                    Km = reactions.loc[reactions['Km'].notnull(),
                                       ['ID', 'Km']]
                    Km = Km.astype(str).apply(lambda x:
                                              '   '.join(x), axis=1)
                    Km = '    ' + Km.astype(str)

                if hill_flag:
                    Hill = reactions.loc[reactions['Hill'].notnull(),
                                         ['ID', 'Hill']]
                    Hill = Hill.astype(str).apply(lambda x:
                                                  '   '.join(x), axis=1)
                    Hill = '    ' + Hill.astype(str)
                    
                if os.path.isfile(model_id + '.cmd'):
                    os.remove(model_id + '.cmd')

                with open((model_id + '.cmd'), 'a') as f:
                    
                    f.write('SMATRIX  ' + str(len(metabolites)) +
                            '  ' + str(len(reactions)) + '\n')
                    smat.to_csv(f, mode='a', header=False, index=False)
                    f.write(r'//' + '\n')

                    f.write('BOUNDS ' +
                            str(default_bounds[0]) + ' ' +
                            str(default_bounds[1]) + '\n')
                    bnd.to_csv(f, mode='a', header=False, index=False)
                    f.write(r'//' + '\n')
            
                    f.write('OBJECTIVE\n' +
                            '    ' + str(objective) + '\n')
                    f.write(r'//' + '\n')

                    f.write('METABOLITE_NAMES\n')
                    met_n.to_csv(f, mode='a', header=False, index=False)
                    f.write(r'//' + '\n')
            
                    f.write('REACTION_NAMES\n')
                    rxn_n.to_csv(f, mode='a', header=False, index=False)
                    f.write(r'//' + '\n')

                    f.write('EXCHANGE_REACTIONS\n')
                    f.write(' ' + exch_r + '\n')
                    f.write(r'//' + '\n')

                    if vmax_flag:
                        f.write('VMAX_VALUES ' +
                                str(default_vmax) + '\n')
                        Vmax.to_csv(f, mode='a', header=False, index=False)
                        f.write(r'//' + '\n')

                    if km_flag:
                        f.write('KM_VALUES ' +
                                str(default_km) + '\n')
                        Km.to_csv(f, mode='a', header=False, index=False)
                        f.write(r'//' + '\n')

                    if hill_flag:
                        f.write('HILL_VALUES ' +
                                str(default_hill) + '\n')
                        Hill.to_csv(f, mode='a', header=False, index=False)
                        f.write(r'//' + '\n')
                    
                    f.write('OBJECTIVE_STYLE\n' + obj_style + '\n')
                    f.write(r'//' + '\n')

                    f.write('OPTIMIZER ' + optimizer + '\n')
                    f.write(r'//' + '\n')

            self.models = [os.path.basename(k) for k in self.models]
                    
            # '''----------- GRID ------------------------------------------'''
            try:
                self.grid = [int(i) for i in f_lines[2].split()[1:]]
                if len(self.grid) < 2:
                    raise CorruptLine
            except CorruptLine:
                print('\n ERROR CorruptLine: Only ' + str(len(self.grid)) +
                      ' dimension(s) specified for world grid')
                
            # '''----------- MEDIA DESCRIPTION -----------------------------'''
            lin_media = re.split('world_media',
                                 filedata_string)[0].count('\n') + 1
            lin_media_end = next(x for x in end_blocks if x > lin_media)
            
            media_names = []
            media_conc = []
            for i in range(lin_media, lin_media_end):
                metabolite = f_lines[i].split()
                media_names.append(metabolite[0])
                media_conc.append(float(metabolite[1]))

            self.media['metabolite'] = media_names
            self.media['init_amount'] = media_conc
            
            # '''----------- MEDIA DIFFUSION -------------------------------'''
            lin_diff = re.split('diffusion_constants',
                                filedata_string)[0].count('\n')
            lin_diff_end = next(x for x in end_blocks if x > lin_diff)

            self.global_diff = float(re.findall(r'\S+', f_lines[lin_diff].
                                                strip())[1])
            try:
                for i in range(lin_diff+1, lin_diff_end):
                    diff_spec = [float(x) for x in f_lines[i].split()]
                    if diff_spec[0] > len(self.media.metabolite)-1:
                        raise UnallocatedMetabolite
                    else:
                        self.media.loc[int(diff_spec[0]),
                                       'diff_c'] = diff_spec[1]
            except UnallocatedMetabolite:
                print('\n ERROR UnallocatedMetabolite: Some diffusion ' +
                      'values correspond to unallocated metabolites')

            # '''----------- MEDIA REFRESH----------------------------------'''
            # .. global refresh values
            lin_refr = re.split('refresh',
                                filedata_string)[0].count('\n')
            lin_refr_end = next(x for x in end_blocks if x > lin_refr)
            
            g_refresh = [float(x) for x in f_lines[lin_refr].split()[1:]]

            try:
                if len(g_refresh) != len(media_names):
                    raise CorruptLine
                else:
                    self.media['g_refresh'] = g_refresh
            except CorruptLine:
                print('\n ERROR CorruptLine: Number of global refresh ' +
                      'values does not match number of \nmedia metabolites ' +
                      'in provided layout file')

            # .. local refresh values
            lin_refr += 1
            try:
                for i in range(lin_refr, lin_refr_end):
                    refr_spec = [float(x) for x in f_lines[i].split()]
                    if len(refr_spec) != len(self.media.metabolite)+2:
                        raise CorruptLine
                    elif (refr_spec[0] >= self.grid[0] or
                          refr_spec[1] >= self.grid[1]):
                        raise OutOfGrid
                    else:
                        self.local_refresh.append(refr_spec)
                    
            except CorruptLine:
                print('\n ERROR CorruptLine: Some local "refresh" lines ' +
                      'have a wrong number of entries')
            except OutOfGrid:
                print('\n ERROR OutOfGrid: Some local "refresh" lines ' +
                      'have coordinates that fall outside of the \ndefined ' +
                      'grid')

            # '''----------- STATIC MEDIA ----------------------------------'''
            # .. global static values
            lin_static = re.split('static',
                                  filedata_string)[0].count('\n')
            lin_stat_end = next(x for x in end_blocks if x > lin_static)

            g_static = [float(x) for x in f_lines[lin_static].split()[1:]]
            try:
                if len(g_static) != 2*len(self.media.metabolite):
                    raise CorruptLine
                else:
                    self.media.loc[:, 'g_static'] = [int(x)
                                                     for x in g_static[0::2]]
                    self.media.loc[:, 'g_static_val'] = [float(x) for x in
                                                         g_static[1::2]]
            except CorruptLine:
                print('\nERROR CorruptLine: Wrong number of global ' +
                      'static values')
                
            # .. local static values
            lin_static += 1
            try:
                for i in range(lin_static, lin_stat_end):
                    stat_spec = [float(x) for x in f_lines[i].split()]
                    if len(stat_spec) != (2*len(self.media.metabolite))+2:
                        raise CorruptLine
                    elif (stat_spec[0] >= self.grid[0] or
                          stat_spec[1] >= self.grid[1]):
                        raise OutOfGrid
                    else:
                        self.local_static.append(stat_spec)
                        
            except CorruptLine:
                print('\n ERROR CorruptLine: Wrong number of local static ' +
                      'values at some lines')
            except OutOfGrid:
                print('\n ERROR OutOfGrid: Some local "static" lines have ' +
                      ' coordinates that fall outside of the defined grid')

            # '''----------- INITIAL POPULATION ----------------------------'''
            lin_initpop = re.split('initial_pop',
                                   filedata_string)[0].count('\n')
            lin_initpop_end = next(x for x in end_blocks if x > lin_initpop)

            g_initpop = f_lines[lin_initpop].split()[1:]
            
            if (len(g_initpop) > 0 and g_initpop[0] in ['random',
                                                        'random_rect',
                                                        'filled',
                                                        'filled_rect',
                                                        'square']):
                self.initial_pop_type = g_initpop[0]
                self.initial_pop = [float(x) for x in g_initpop[1:]]
            else:
                self.initial_pop_type = 'custom'
                
                # .. local initial population values
                lin_initpop += 1
                try:
                    for i in range(lin_initpop, lin_initpop_end):
                        ipop_spec = [float(x) for x in
                                     f_lines[lin_initpop].split()]
                        if len(ipop_spec) != len(self.models)+2:
                            raise CorruptLine
                        if (ipop_spec[0] >= self.grid[0] or
                                ipop_spec[1] >= self.grid[1]):
                            raise OutOfGrid
                        else:
                            self.initial_pop.append(ipop_spec)
                except CorruptLine:
                    print('Problem at some initial population lines')
                except OutOfGrid:
                    print('Some initial population values' +
                          ' fall outside of the defined grid')
        else:
            ''' Make a default layout with media components from the
                provided model '''
            if not isinstance(input_obj, list):
                input_obj = [input_obj]
            self.models = [x.model_name for x in input_obj]
            self.grid = [1, 1]
            self.media = pd.DataFrame(columns=['metabolite',
                                               'init_amount',
                                               'diff_c',
                                               'g_static',
                                               'g_static_val',
                                               'g_refresh'])
            
            # write models in current working folder and extract metabolites
            exchanged_metab = []
            for i in input_obj:
                i.write_model()
                exchr = i.reactions.loc[i.reactions.EXCH, 'ID'].tolist()
                exchm = i.smat.loc[i.smat.rxn.isin(exchr),
                                   'metabolite'].tolist()
                exchm = i.metabolites.iloc[[x-1 for x in exchm]][
                    'METABOLITE_NAMES'].tolist()
                exchanged_metab.append(exchm)
                
            # using set comprehension here to remove duplicates automatically
            exchanged_metab = list({item
                                    for sublist in exchanged_metab
                                    for item in sublist})
            self.media['metabolite'] = exchanged_metab
            self.media['init_amount'] = 0
            self.media['g_static'] = 0
            self.media['g_static_val'] = 0
            self.media['g_refresh'] = 0
            self.global_diff = 1e-6
            self.local_refresh = []
            self.local_static = []
            self.initial_pop_type = 'custom'
            self.initial_pop = [[0] * 2 + [1e-9] * len(self.models)]
            # TODO: add all ions unlimited to media
            
    def write_layout(self, outfile):
        ''' Write the layout in a file'''

        if os.path.isfile(outfile):
            os.remove(outfile)
        
        lyt = open(outfile, 'a')
        
        lyt.write('model_file ' +
                  '.cmd '.join(self.models) +
                  '.cmd\n')
        lyt.write('  model_world\n')
        
        lyt.write('    grid_size ' +
                  ' '.join([str(x) for x in self.grid]) + '\n')
            
        lyt.write('    world_media\n')
        for i in range(0, len(self.media)):
            lyt.write('      ' + self.media.metabolite[i] +
                      ' ' + str(self.media.init_amount[i]) + '\n')
        lyt.write(r'    //' + '\n')
            
        lyt.write('    diffusion_constants ' +
                  str(self.global_diff) +
                  '\n')
        for i in range(0, len(self.media)):
            if not math.isnan(self.media.diff_c[i]):
                lyt.write('      ' + str(i) + ' ' +
                          str(self.media.diff_c[i]) + '\n')
        lyt.write(r'    //' + '\n')
                
        lyt.write('    media_refresh ' +
                  ' '.join([str(x) for x in self.media.
                            g_refresh.tolist()]) +
                  '\n')
        for i in range(0, len(self.local_refresh)):
            lyt.write('      ' +
                      ' '.join([str(x) for x in self.local_refresh[i]]) +
                      '\n')
        lyt.write(r'    //' + '\n')

        g_static_line = [None]*(len(self.media)*2)
        g_static_line[::2] = self.media.g_static
        g_static_line[1::2] = self.media.g_static_val
        lyt.write('    static_media ' +
                  ' '.join([str(x) for x in g_static_line]) + '\n')
        
        for i in range(0, len(self.local_static)):
            lyt.write('      ' +
                      ' '.join([str(x) for x in self.local_static[i]]) +
                      '\n')
        lyt.write(r'    //' + '\n')
        lyt.write(r'  //' + '\n')

        if (self.initial_pop_type == 'custom'):
            lyt.write('  initial_pop\n')
            for i in self.initial_pop:
                lyt.write('    ' + str(int(i[0])) + ' ' + str(int(i[1])) +
                          ' ' + ' '.join([str(x) for x in i[2:]]) +
                          '\n')
        else:
            # TODO: test this part and fix, probably not functional currently
            lyt.write('  initial_pop ' + self.initial_pop_type +
                      ' '.join([str(x) for x in self.initial_pop]) +
                      '\n')
        lyt.write(r'  //' + '\n')
        lyt.write(r'//' + '\n')
        lyt.close()

    def update_media(self):
        # TODO: update media with all exchangeable metabolites from all models
        pass
    
    def add_model(self, model):
        self.models.append(model.model_name)
        self.update_media()
    
        
class params:

    def __init__(self, global_params=None, package_params=None):
        
        self.all_params = {'BiomassLogName': 'biomass.txt',
                           'BiomassLogRate': 1,
                           'FluxLogName': 'flux_out',
                           'FluxLogRate': 5,
                           'MediaLogName': 'media_out',
                           'MediaLogRate': 5,
                           'TotalBiomassLogName': 'total_biomass_out.txt',
                           'maxCycles': 100,
                           'saveslideshow': False,
                           'totalBiomassLogRate': 1,
                           'useLogNameTimeStamp': False,
                           'writeBiomassLog': False,
                           'writeFluxLog': False,
                           'writeMediaLog': False,
                           'writeTotalBiomassLog': True,
                           'batchDilution': False,
                           'dilFactor': 10,
                           'dilTime': 2,
                           'cellSize': 1e-13,
                           'allowCellOverlap': True,
                           'deathRate': 0,
                           'defaultHill': 1,
                           'defaultKm': 0.01,
                           'defaultVmax': 10,
                           'defaultAlpha': 1,
                           'defaultW': 10,
                           'defaultDiffConst': 1e-5,
                           'exchangestyle': 'Monod Style',
                           'flowDiffRate': 3e-9,
                           'growthDiffRate': 0,
                           'maxSpaceBiomass': 0.1,
                           'minSpaceBiomass': 0.25e-10,
                           'numDiffPerStep': 10,
                           'numRunThreads': 1,
                           'showCycleCount': True,
                           'showCycleTime': False,
                           'spaceWidth': 0.02,
                           'timeStep': 0.1,
                           'toroidalWorld': False,
                           'simulateActivation': False,
                           'activateRate': 0.001,
                           'randomSeed': 0,
                           'colorRelative': True,
                           'slideshowColorRelative': True,
                           'slideshowRate': 1,
                           'slideshowLayer': 0,
                           'slideshowExt': 'png',
                            # 'biomassMotionStyle': 'Diffusion' +
                            # '2D(Crank-Nicolson)', TODO: this not working
                           'numExRxnSubsteps': 5,
                           'costlyGenome': True,
                           'geneFractionalCost': 1e-4,
                           'evolution': False,
                           'mutRate': 1e-5,
                           'addRate': 1e-5}
        self.all_params = dict(sorted(self.all_params.items(),
                                      key=lambda x: x[0]))
        
        self.all_type = {'BiomassLogName': 'global',
                         'BiomassLogRate': 'global',
                         'FluxLogName': 'global',
                         'FluxLogRate': 'global',
                         'MediaLogName': 'global',
                         'MediaLogRate': 'global',
                         'TotalBiomassLogName': 'global',
                         'maxCycles': 'package',
                         'saveslideshow': 'global',
                         'totalBiomassLogRate': 'global',
                         'useLogNameTimeStamp': 'global',
                         'writeBiomassLog': 'global',
                         'writeFluxLog': 'global',
                         'writeMediaLog': 'global',
                         'writeTotalBiomassLog': 'global',
                         'batchDilution': 'global',
                         'dilFactor': 'global',
                         'dilTime': 'global',
                         'cellSize': 'global',
                         'allowCellOverlap': 'package',
                         'deathRate': 'package',
                         'defaultHill': 'package',
                         'defaultKm': 'package',
                         'defaultVmax': 'package',
                         'defaultW': 'package',
                         'defaultAlpha': 'package',
                         'defaultDiffConst': 'package',
                         'exchangestyle': 'package',
                         'flowDiffRate': 'package',
                         'growthDiffRate': 'package',
                         'maxSpaceBiomass': 'package',
                         'minSpaceBiomass': 'package',
                         'numDiffPerStep': 'package',
                         'numRunThreads': 'package',
                         'showCycleCount': 'package',
                         'showCycleTime': 'package',
                         'spaceWidth': 'package',
                         'timeStep': 'package',
                         'toroidalWorld': 'package',
                         'simulateActivation': 'global',
                         'activateRate': 'global',
                         'randomSeed': 'global',
                         'colorRelative': 'global',
                         'slideshowColorRelative': 'global',
                         'slideshowRate': 'global',
                         'slideshowLayer': 'global',
                         'slideshowExt': 'global',
                         'biomassMotionStyle': 'package',
                         'numExRxnSubsteps': 'package',
                         'costlyGenome': 'global',
                         'geneFractionalCost': 'global',
                         'evolution': 'package',
                         'mutRate': 'package',
                         'addRate': 'package'}
        self.all_type = dict(sorted(self.all_type.items(),
                                    key=lambda x: x[0]))

        # .. parse parameters files to python type variables
        if global_params is not None:
            with open(global_params) as f:
                for line in f:
                    if '=' in line:
                        k, v = line.split(' = ')
                        if v.strip() == 'true':
                            self.all_params[k.strip()] = True
                        elif v.strip() == 'false':
                            self.all_params[k.strip()] = False
                        elif v.strip().isdigit():
                            self.all_params[k.strip()] = int(v.strip())
                        elif isfloat(v.strip()):
                            self.all_params[k.strip()] = float(v.strip())
                        else:
                            self.all_params[k.strip()] = v.strip()

        if package_params is not None:
            with open(package_params) as f:
                for line in f:
                    if '=' in line:
                        k, v = line.split(' = ')
                        if v.strip() == 'true':
                            self.all_params[k.strip()] = True
                        elif v.strip() == 'false':
                            self.all_params[k.strip()] = False
                        elif v.strip().isdigit():
                            self.all_params[k.strip()] = int(v.strip())
                        elif isfloat(v.strip()):
                            self.all_params[k.strip()] = float(v.strip())
                        else:
                            self.all_params[k.strip()] = v.strip()

        # Additional processing.
        # If evolution is true, we dont want to write the total biomass log
        if self.all_params['evolution']:
            self.all_params['writeTotalBiomassLog'] = False

    ''' write parameters files; method probably only used by class comets'''
    def write_params(self, out_glb, out_pkg):

        if os.path.isfile(out_glb):
            os.remove(out_glb)

        if os.path.isfile(out_pkg):
            os.remove(out_pkg)

        # convert booleans to java format before writing
        towrite_params = {}
        for k, v in self.all_params.items():
            if v is True:
                towrite_params[k] = 'true'
            elif v is False:
                towrite_params[k] = 'false'
            else:
                towrite_params[k] = str(v)

        with open(out_glb, 'a') as glb, open(out_pkg, 'a') as pkg:
            for k, v in towrite_params.items():
                if self.all_type[k] == 'global':
                    glb.writelines(k + ' = ' + v + '\n')
                else:
                    pkg.writelines(k + ' = ' + v + '\n')


class comets:
    '''
    This class sets up an environment with all necessary for
    a comets simulation to run, runs the simulation, and stores the output
    data from it.
    '''
    def __init__(self, layout, parameters, working_dir=''):
        
        # define instance variables
        self.working_dir = os.getcwd() + '/' + working_dir
        self.GUROBI_HOME = os.environ['GUROBI_HOME']
        self.COMETS_HOME = os.environ['COMETS_HOME']

        self.VERSION = 'comets_evo'
        self.JAVA_CLASSPATH = (self.GUROBI_HOME +
                               '/lib/gurobi.jar:' +
                               self.COMETS_HOME +
                               '/lib/junit/junit-4.12.jar:' +
                               self.COMETS_HOME +
                               '/junit/hamcrest-core-1.3.jar:' +
                               self.COMETS_HOME +
                               '/lib/jogl/jogamp-all-platforms/' +
                               'jar/jogl-all.jar:' +
                               self.COMETS_HOME +
                               '/lib/jogl/jogamp-all-platforms/' +
                               'jar/gluegen-rt.jar:' +
                               self.COMETS_HOME +
                               '/lib/jogl/jogamp-all-platforms/' +
                               'jar/gluegen.jar:' +
                               self.COMETS_HOME +
                               '/lib/jogl/jogamp-all-platforms/jar/' +
                               'gluegen-rt-natives-linux-amd64.jar:' +
                               self.COMETS_HOME +
                               '/lib/jogl/jogamp-all-platforms/jar/' +
                               'jogl-all-natives-linux-amd64.jar:' +
                               self.COMETS_HOME +
                               '/lib/JMatIO/lib/jamtio.jar:' +
                               self.COMETS_HOME +
                               '/lib/JMatIO/JMatIO-041212/lib/jmat.jar:' +
                               self.COMETS_HOME +
                               '/lib/colt/lib/concurrent.jar:' +
                               self.COMETS_HOME +
                               '/lib/colt/lib/colt.jar:' +
                               self.COMETS_HOME +
                               '/lib/commons-lang3-3.7/' +
                               'commons-lang3-3.7.jar:' +
                               self.COMETS_HOME + '/bin/' +
                               self.VERSION + '.jar')

        self.layout = layout
        self.parameters = parameters
        
        # dealing with output files
        self.parameters.all_params['useLogNameTimeStamp'] = False
        self.parameters.all_params['TotalBiomassLogName'] = (
            'total_biomass_log_' + hex(id(self)))
        self.parameters.all_params['BiomassLogName'] = (
            'biomass_log_' + hex(id(self)))
        self.parameters.all_params['FluxLogName'] = (
            'flux_log_' + hex(id(self)))
        self.parameters.all_params['MediaLogName'] = (
            'media_log_' + hex(id(self)))

    def run(self):
        print('\nRunning COMETS simulation ...')
        # write the files for comets in working_dir
        c_global = self.working_dir + '.current_global'
        c_package = self.working_dir + '.current_package'
        c_script = self.working_dir + '.current_script'

        self.layout.write_layout(self.working_dir + '.current_layout')
        self.parameters.write_params(c_global, c_package)

        if os.path.isfile(c_script):
            os.remove(c_script)
        with open(c_script, 'a') as f:
            f.write('load_comets_parameters ' + c_global + '\n')
            f.writelines('load_package_parameters ' + c_package + '\n')
            f.writelines('load_layout ' + self.working_dir +
                         '.current_layout')
            
        # simulate
        self.cmd = ('java -classpath ' + self.JAVA_CLASSPATH +
                    # ' -Djava.library.path=' + self.D_JAVA_LIB_PATH +
                    ' edu.bu.segrelab.comets.Comets -loader' +
                    ' edu.bu.segrelab.comets.fba.FBACometsLoader' +
                    ' -script ' + c_script)
        
        p = sp.Popen(self.cmd, shell=True, stdout=sp.PIPE, stderr=sp.STDOUT)

        self.run_output, self.run_errors = p.communicate()
        self.run_output = self.run_output.decode()

        if self.run_errors is not None:
            self.run_errors = self.run_errors.decode()
        else:
            self.run_errors = "STDERR empty."
            
        # clean workspace
        os.remove(c_global)
        os.remove(c_package)
        os.remove(c_script)
        os.remove('.current_layout')
        
        # '''----------- READ OUTPUT ---------------------------------------'''

        # Read total biomass output
        if self.parameters.all_params['writeTotalBiomassLog']:
            tbmf = readlines_file(
                self.parameters.all_params['TotalBiomassLogName'])
            self.total_biomass = pd.DataFrame([re.split(r'\t+', x.strip())
                                               for x in tbmf],
                                              columns=['cycle'] +
                                              self.layout.models)
            
        # Read flux
        if self.parameters.all_params['writeFluxLog']:
            self.fluxes = []
            tff = readlines_file(
                self.parameters.all_params['FluxLogName'])
            for i in tff:
                self.fluxes.append(re.findall(r'\d+',
                                              re.search(r'\{(.*)\}',
                                                        i).group(1)) +
                                   [float(x)
                                    for x in re.search(r'\[(.*)\]',
                                                       i).group(1).split()])

        # Read spatial biomass log
        if self.parameters.all_params['writeBiomassLog']:
            biomass_out_file = 'biomass_log_' + hex(id(self))
            self.biomass = pd.read_csv(biomass_out_file,
                                       header=None, delimiter=r'\s+',
                                       names=['Cycle', 'x', 'y',
                                              'species', 'biomass'])

        # Read evolution-related logs
        if self.parameters.all_params['evolution']:
            evo_out_file = 'biomass_log_' + hex(id(self))
            self.evolution = pd.read_csv(evo_out_file,
                                         header=None, delimiter=r'\s+',
                                         names=['Cycle', 'x', 'y',
                                                'species', 'biomass'])
            genotypes_out_file = 'GENOTYPES_biomass_log_' + hex(id(self))
            self.genotypes = pd.read_csv(genotypes_out_file,
                                         header=None, delimiter=r'\s+',
                                         names=['Ancestor',
                                                'Mutation',
                                                'Species'])

        print('Done!')

        # TODO read media logs
        # TODO read spatial biomass logs

# os.chdir('/home/djordje/Dropbox/comets_py_working_dir') 
# my_params = params('files/global_params', 'files/package_params')
# my_layout = layout('files/layout_blueprint')
# my_comets = comets(my_layout, my_params)
# my_comets.run()
