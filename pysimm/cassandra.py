# ******************************************************************************
# pysimm.cassandra module
# ******************************************************************************
#
# ******************************************************************************
# License
# ******************************************************************************
# The MIT License (MIT)

from StringIO import StringIO
import subprocess
from subprocess import call, Popen, PIPE
import os
import numpy as np
import random
import logging
import types
from collections import Iterable, OrderedDict
import pysimm
from pysimm import utils, system

kcalMol2K = 503.22271716452
isomp = False
if isomp:
    CASSANDRA_EXEC = os.environ.get('CASSANDRA_OMP_EXEC')
else:
    CASSANDRA_EXEC = os.environ.get('CASSANDRA_EXEC')

# Creating a logger instance and send its output to console 'deafault'
logging.basicConfig(level=logging.INFO)


def check_cs_exec():
    if CASSANDRA_EXEC is None:
        print('Please specify the OS environment variable ''CASSANDRA_EXEC'' that points to '
              'CASSANDRA compiled binary file ( cassandra_{compiler-name}[_openMP].exe )')
        return False
    # else:
    #     try:
    #         stdout, stderr = Popen('CASSANDRA_EXEC', stdin=PIPE, stdout=PIPE, stderr=PIPE).communicate()
    #         return True
    #     except OSError:
    #         print('Seems the environment variable ''CASSANDRA_EXEC'' is not configured properely. '
    #               'Please check the OS environment variable ''CASSANDRA_EXEC'' it should point '
    #               'to CASSANDRA compiled binary file ( cassandra_{compiler-name}[_openMP].exe ) ')
    #         return False

check_cs_exec()


class GCMC(object):

    def __init__(self, fxd_sst=None, mc_sst=None, **kwargs):
        # Text output stream, empty at the beggining
        self.input = ''
        self.logger = logging.getLogger('GCMC')
        self.adsorob_path = '/home/alleksd/Work/pysimm/dat/csndra_data'

        self.props_file = 'gcmc_params.inp'
        self.adsorbers = kwargs.get('adsorbers') or None
        self.out_folder = kwargs.get('out_folder') or os.getcwd()

        # Dictionary containing records that are directly will be sent to the .inp file
        self.props = OrderedDict()

        # Static (unchangeable) properties
        self.props['Sim_Type'] = InpSpec('Sim_Type', 'gcmc', 'gcmc')

        # Molecule configuration files describing all species of the system.
        # They **absolutely** needed to start calculation
        mol_files = OrderedDict()

        fixed_mcf = 'fixed_syst.mcf'
        self.fxd_sst = fxd_sst
        self.fxd_sst.zero_charge()
        self.fixed_syst_mcf_file = None
        if fxd_sst:
            self.fixed_syst_mcf_file = os.path.join(self.out_folder, fixed_mcf)
            mol_files['file1'] = [fixed_mcf, 1]
            fs_count = 1
        else:
            fs_count = 0

        self.mc_sst = mc_sst
        if mc_sst:
            mol_files = mc_sst.update_props(mol_files)

        if kwargs.get('Molecule_Files'):
            mol_files = OrderedDict(sorted(kwargs.get('Molecule_Files').items()))

        # Raising an error and stop execution if no MCF information is provided
        if (mc_sst is None) and (not kwargs.get('Molecule_Files')):
            self.logger.error('The molecular configuration files of gas molecules for simulation are not set. '
                              'Nothing to simulate. Exiting...')
            exit(1)

        n_spec = len(mol_files)
        self.props['Nbr_Species'] = InpSpec('Nbr_Species', n_spec, n_spec)
        self.props['Molecule_Files'] = InpSpec('Molecule_Files', mol_files, None, **{'new_line': True})

        # Simple (one-value) dynamic properties
        self.props['Run_Name'] = InpSpec('Run_Name', kwargs.get('Run_Name'), 'gcmc_simulation')
        self.props['Temperature_Info'] = InpSpec('Temperature_Info', kwargs.get('Temperature_Info'), 273)
        self.props['Average_Info'] = InpSpec('Average_Info', kwargs.get('Average_Info'), 1)
        self.props['Pair_Energy'] = InpSpec('Pair_Energy', kwargs.get('Pair_Energy'), 'true')
        self.props['Rcutoff_Low'] = InpSpec('Rcutoff_Low', kwargs.get('Rcutoff_Low'), 0.0)
        self.props['Mixing_Rule'] = InpSpec('Mixing_Rule', kwargs.get('Mixing_Rule'), 'lb')
        self.props['Bond_Prob_Cutoff'] = InpSpec('Bond_Prob_Cutoff', kwargs.get('Bond_Prob_Cutoff'), 1e-10)
        self.props['Chemical_Potential_Info'] = InpSpec('Chemical_Potential_Info', mc_sst.chem_pot,
                                                        [-25] * (n_spec - fs_count))

        self.props['Seed_Info'] = InpSpec('Seed_Info', kwargs.get('Seed_Info'),
                                          [random.randint(int(1e+7), int(1e+8 - 1)),
                                           random.randint(int(1e+7), int(1e+8 - 1))])

        # Multiple-value one/many line dynamic properties
        self.props['Run_Type'] = InpSpec('Run_Type', kwargs.get('Run_Type'),
                                         OrderedDict([('type', 'Equilibration'),
                                                      ('steps', 100)]))

        self.props['Charge_Style'] = InpSpec('Charge_Style', kwargs.get('Charge_Style'),
                                             OrderedDict([('type', 'coul'),
                                                          ('sum_type', 'ewald'),
                                                          ('cut_val', 15.00),
                                                          ('accuracy', 1e-5)]))

        self.props['VDW_Style'] = InpSpec('VDW_Style', kwargs.get('VDW_Style'),
                                          OrderedDict([('type', 'lj'),
                                                       ('cut_type', 'cut_tail'),
                                                       ('cut_val', 15.00)]))

        self.props['Simulation_Length_Info'] = InpSpec('Simulation_Length_Info', kwargs.get('Simulation_Length_Info'),
                                                       OrderedDict([('units', 'steps'),
                                                                    ('prop_freq', 100),
                                                                    ('coord_freq', 1000),
                                                                    ('run', 10000)]),
                                                       **{'write_headers': True, 'new_line': True})
        self.props['CBMC_Info'] = InpSpec('CBMC_Info', kwargs.get('CBMC_Info'),
                                          OrderedDict([('kappa_ins', 12),
                                                       ('kappa_dih', 12),
                                                       ('rcut_cbmc', 6.5)]),
                                          **{'write_headers': True, 'new_line': True})

        self.props['Box_Info'] = InpSpec('Box_Info', kwargs.get('Box_Info'),
                                         OrderedDict([('box_count', 1),
                                                      ('box_type', 'cubic'),
                                                      ('box_size', 100)]),
                                         **{'new_line': True})

        # Order of the next three items is IMPORTANT! Check the CASSANDRA spec file for further info
        limits = [0.36] * n_spec
        if fxd_sst:
            limits[0] = 0
        self.props['Prob_Translation'] = InpProbSpec('Prob_Translation', kwargs.get('Prob_Translation'),
                                                     OrderedDict([('tot_prob', 0.4),
                                                                  ('limit_vals', limits)]),
                                                     **{'new_line': True, 'indicator': 'start'})

        tps = ['cbmc'] * n_spec
        if fxd_sst:
            tps[0] = 'none'
        self.props['Prob_Insertion'] = InpProbSpec('Prob_Insertion', kwargs.get('Prob_Insertion'),
                                                   OrderedDict([('tot_prob', 0.3),
                                                                ('types', tps)]),
                                                   **{'new_line': True})

        self.props['Prob_Deletion'] = InpProbSpec('Prob_Deletion',
                                                  kwargs.get('Prob_Deletion'), 0.3, **{'indicator': 'end'})

        # Synchronzing "start type" .inp record
        self.fixed_syst__xyz_file = None
        pops_list = [0] * n_spec
        st_type = 'make_config'
        loc_coords = ''
        if fxd_sst:
            pops_list[0] = 1
            loc_coords = '_fixed_atoms_coords.xyz'
            self.fixed_syst__xyz_file = os.path.join(self.out_folder, loc_coords)
            st_type = 'read_config'
        start_conf_dict = OrderedDict([('start_type', st_type), ('species', pops_list), ('file_name', loc_coords)])

        # if write_statics:
        #     start_conf_dict['file_name'] = loc_coords
        self.props['Start_Type'] = InpSpec('Start_Type', None, start_conf_dict)

        # Synchronzing Fragment files:
        frag_files = OrderedDict()
        if mc_sst:
            mc_sst.temperature = self.props['Temperature_Info'].value
            frag_files = mc_sst.update_frag_record(frag_files)
        if kwargs.get('Fragment_Files'):
            frag_files = OrderedDict(sorted(kwargs.get('Fragment_Files').items()))
        if (mc_sst is None) and (not kwargs.get('Fragment_Files')):
            self.logger.error('Cannot set the fragment files of gas molecules for simulation')
            exit(1)
        self.props['Fragment_Files'] = InpSpec('Fragment_Files', frag_files, None, **{'new_line': True})
        self.props['Property_Info 1'] = InpSpec('Property_Info 1', kwargs.get('Property_Info'),
                                                None, **{'new_line': True})

    def write(self):
        for key in self.props.keys():
            if self.props[key].value is not None:
                self.input += '{:}\n'.format(self.props[key].to_string())

        self.input += '\nEND'
        # Initializing output stream
        self.logger.info('Writing CASSANDRA .inp file to "{:}"...'.format(self.props_file))
        out_stream = open(self.props_file, 'w+')
        out_stream.write('{:}'.format(self.input))
        out_stream.close()
        self.logger.info('File: "{:}" was created sucsessfully'.format(self.props_file))

    def __check_params__(self):
        # Synchronizing the simulation box parameters
        dx = self.fxd_sst.dim.dx
        dy = self.fxd_sst.dim.dy
        dz = self.fxd_sst.dim.dz
        if (dx == dy) and (dy == dz):
            box_type = 'cubic'
            box_dims = str(dx)
        else:
            box_type = 'orthogonal'
            box_dims = '{0:} {1:} {2:}'.format(dx, dy, dz)

        upd_vals = OrderedDict([('box_count', 1),
                                ('box_type', box_type),
                                ('box_size', box_dims)])
        if ('Box_Info' in self.props.keys()) and isinstance(self.props['Box_Info'], InpSpec):
            self.props['Box_Info'] = InpSpec('Box_Info', upd_vals, None, **{'new_line': True})
        else:
            self.props['Box_Info'] = upd_vals


class InpSpec(object):
    def __init__(self, key, value, default, **kwargs):
        self.key = key
        self.write_headers = kwargs.get('write_headers')
        self.is_new_line = kwargs.get('new_line')

        self.value = value
        if value:
            if isinstance(default, types.DictType):
                # Add from default structure all properties that were not defined by user
                for ky in value.keys():
                    default[ky] = value[ky]
                self.value = default
            else:
                self.value = value
        elif value == []:
            self.value = []
        else:
            # If nothing was passed write default
            self.value = default

    def to_string(self):
        if self.value:
            result = '# {:}\n'.format(self.key)
            # Strings
            if isinstance(self.value, types.StringTypes):
                result += str(self.value)
            # Dictionaries
            elif isinstance(self.value, types.DictType):
                for ks in list(self.value.keys()):
                    if self.write_headers:
                        result += ks + '  '

                    tmp = self.value[ks]
                    if (isinstance(tmp, Iterable)) & (not isinstance(tmp, types.StringTypes)):
                        result += '   '.join(str(p) for p in tmp)
                    else:
                        result += str(tmp)

                    if self.is_new_line:
                        result += '\n'
                    else:
                        result += ' '
                result = result[:-1]  # Remove the very last new line character
            # Lists
            elif isinstance(self.value, Iterable):
                for elem in self.value:
                    if isinstance(elem, Iterable):
                        subresult = ''
                        for subelem in elem:
                            subresult = subresult + str(subelem) + ' '
                    else:
                        subresult = str(elem) + ' '
                    result += subresult
            # Simple types
            else:
                result += str(self.value)
            result += '\n!{:^^20}\n'.format('')
            return result


class InpProbSpec(InpSpec):
    def __init__(self, key, value, default, **kwargs):
        super(InpProbSpec, self).__init__(key, value, default, **kwargs)

    def to_string(self):
        tmp = super(InpProbSpec, self).to_string()
        if self.key == 'Prob_Translation':
            tmp = '# Move_Probability_Info\n\n' + tmp
        elif self.key == 'Prob_Deletion':
            tmp += '\n# Done_Probability_Info\n'
        return tmp


class InpFileSpec(InpSpec):
    def __init__(self, key, value, default, **kwargs):
        super(InpFileSpec, self).__init__(key, value, default, **kwargs)

        the_name = self.value['file_name']
        # Check the existence of the file-type variables. Continue only when the files exist!
        if the_name & (not os.path.isfile(the_name)):
            print("ERROR: cannot find a file " + the_name + ".\n Please specify the file.\n" + " Aborting execution")



class InpMcfSpec(InpSpec):
    def __init__(self, key, value, default, **kwargs):
        super(InpMcfSpec, self).__init__(key, value, default, **kwargs)


class McSystem(object):
    def __init__(self, s, **kwargs):

        self.sst = self.__make_iterable__(s)
        for sst in self.sst:
            sst.zero_charge()
        self.file_store = '/home/alleksd/Work/pysimm/Examples/09_cassandra/gcmc_tests'
        self.max_ins = self.__make_iterable__(kwargs.get('max_ins')) or 5000
        self.chem_pot = self.__make_iterable__(kwargs.get('chem_pot'))
        self.mcf_file = []
        self.frag_file = []
        self.temperature = None

    def update_cp(self, cp):
        return self.chem_pot

    def update_props(self, props):
        self.generate_mcf()
        offset = len(props)
        for (mcf, ins, count) in zip(self.mcf_file, self.max_ins, range(1 + offset, len(self.mcf_file) + 1 + offset)):
            props['file' + str(count)] = [mcf, ins]
        return props

    def update_frag_record(self, frag_record):
        self.__generate_frag_file__()
        for (frags, count) in zip(self.frag_file, range(1, len(self.frag_file) + 1)):
            frag_record['file' + str(count)] = [frags, count]
        return frag_record

    def generate_mcf(self):
        protocol = [True, True, True, False, False, True, True, False]
        for (sstm, count) in zip(self.sst, range(len(self.sst))):
            fullfile = os.path.join(self.file_store, '{:}{:}{:}'.format('particle', str(count + 1), '.mcf'))
            McfWriter(sstm, fullfile, protocol).write()
            self.mcf_file.append(fullfile)

    # Force our fields be iterable (wrap in a list if it contains of only one item)
    def __make_iterable__(self, obj):
        it_obj = obj
        if not isinstance(obj, Iterable):
            it_obj = [obj]
        return it_obj

    # Now is private because it is works only for single-configuration fragment file
    def __generate_frag_file__(self):
        if self.temperature is None:
            self.temperature = 300

        for (sstm, count) in zip(self.sst, range(len(self.sst))):
            fullfile = os.path.join(self.file_store, '{:}{:}{:}'.format('particle', str(count + 1), '.dat'))
            with open(fullfile, 'w+') as out:
                frag_count = 1
                out.write('{:>12d}\n'.format(frag_count))
                out.write('{:>21.14f}{:>21.14f}\n'.format(self.temperature, 0))
                tmplte = '{:<5}{:<24.16f}{:<24.16f}{:<24.16f}\n'
                for prt in sstm.particles:
                    out.write(tmplte.format(prt.type.name, prt.x, prt.y, prt.z))
            self.frag_file.append(fullfile)

    def read_xyz(self):
        print('Not supported yet!')


class Cassandra(object):
    """
    pysimm.cassandra.Cassandra
    Organizational object for Cassandra simulation that is able to run
    e.g. Gibbs Canonical Monte-Carlo (GCMC) simulations (see the GCMC class)

    """

    def __init__(self, **kwargs):
        # Important simulation stuff
        # self.fxd_sst = fxd
        # self.mc_sst = mc

        # Important programmatic stuff
        self.logger = logging.getLogger('CSNDRA')
        self.run_queue = []

    def run(self):
        global CASSANDRA_EXEC

        for task in self.run_queue:
            # Write .inp file
            task.write()
            if task.fixed_syst_mcf_file is not None:
                McfWriter(task.fxd_sst, task.fixed_syst_mcf_file,
                          [True, False, False, False, False, True, False, False]).write()
            if task.fixed_syst__xyz_file is not None:
                task.fxd_sst.write_xyz(task.fixed_syst__xyz_file)
            try:
                self.logger.info('Start execution of the GCMC simulations with CASSANDRA...')
                print('{:.^60}'.format(''))
                print(subprocess.check_output([CASSANDRA_EXEC, task.props_file]))

                # p = Popen([CASSANDRA_EXEC, task.props_file], stdin=PIPE, stdout=PIPE, stderr=PIPE)
                # outp, errst = p.communicate()

                # fileName = task.props['Run_Name'].value + '.xyz'
                # self.logger.info('Updating CASSANDRA system from the file "{:}"...'.format(fileName))
                # self.system = pysimm.system.read_xyz(fileName)

            except OSError as ose:
                self.logger.error('There was a problem calling CASSANDRA executable')
                # raise PysimmError('There was a problem calling CASSANDRA executable'), None, sys.exc_info()[2]
            except IOError as ioe:
                if check_cs_exec():
                    self.logger.error('There was a problem running CASSANDRA. The process started but did not finish'
                                      )
                    # raise PysimmError('There was a problem running CASSANDRA. The process started but did not finish '
                    #                  'successfully. Check the generated log file'), None, sys.exc_info()[2]

                else:
                    self.logger.error('There was a problem running CASSANDRA: seems it is not configured properly.'
                                      'Make sure the CSNDRA_EXEC environment variable is set to the correct CASSANDRA '
                                      'executable path. The current path is set to:\n\n{}'.format(CASSANDRA_EXEC))

    def add_gcmc(self, obj1=None, obj2=None, **kwargs):
        if isinstance(obj1, GCMC):
            new_job = obj1
        elif isinstance(obj1, system.System) or isinstance(obj1, McSystem):
            new_job = GCMC(obj1, obj2, **kwargs)
        else:
            self.logger.error('Unknown GCMC initialization. Please provide either '
                              'correct GCMC parameters or GCMC simulation object')
            exit(1)
        new_job.__check_params__()
        self.run_queue.append(new_job)

    def __write_chk__(self, out_file):
        # Initializing output stream
        if out_file == 'string':
            out_stream = StringIO()
        else:
            out_stream = open(out_file, 'w+')
        sys = self.system  # alias of the System object

        blkSepar = '{:*^75}\n'

        # Writing Translation/rotation/... info
        contNfo = self.props['# Molecule_Files']
        out_stream.write(blkSepar.format('Translation,rotation, dihedral, angle distortion'))
        tmplate = '{t[0]$$}{t[1]$$}{t[2]$$}{t[3]$$}{t[4]$$}\n'

        for i in range(len(contNfo)):
            out_stream.write(tmplate.replace('$$', ':>6d').format(t=map(int, np.insert(np.zeros(4), 0, i + 1))))
            out_stream.write(tmplate.replace('$$', ':>6d').format(t=map(int, np.insert(np.zeros(4), 0, i + 1))))
            # TODO There are some nonzeros in Tylangas .chk file for index 2; check where they come from
            out_stream.write('{t[0]:>23.14E}{t[2]:>23.14E}{t[2]:>23.14E}\n'.format(t=np.zeros(3)))
            out_stream.write('{0:>12d}{0:>12d}\n'.format(0, 0))

        # Small section with total # of MC trials -- it is 0 at the beggining
        out_stream.write(blkSepar.format('# of MC steps'))
        out_stream.write('{:>12d}\n'.format(0))

        # Writing Box-info stuff
        out_stream.write(blkSepar.format('Box info'))
        for box in self.boxes:
            # First 0 in input correspond to the # of trials
            out_stream.write('{0:>12d}\n{1:<18.10f}\n{2:}\n'.format(0, box.vol, box.bxType))
            
            tmpl = '{t[0]&&}{t[1]&&}{t[2]&&}\n'
            tmp = np.diag( [box.x, box.y, box.z] )
            for lines in tmp:
                out_stream.write((tmpl.replace('&&', ':^22.14f')).format(t=lines))

            tmp = np.diag( [1/box.x, 1/box.y, 1/box.z])
            for lines in tmp:
                out_stream.write((tmpl.replace('&&', ':^22.8f')).format(t = lines))
            out_stream.write('{:>18.12f}\n'.format(0))  # TODO: Maximal volume displacement

        # Writing SEEDS !!!111
        out_stream.write(blkSepar.format('SEEDS'))
        out_stream.write('{t[0]:>12d}{t[1]:>12d}{t[2]:>12d}\n{t[3]:>12d}{t[4]:>12d}\n'.format(
                        t=np.random.random_integers(int(1e+7), int(1e+8 - 1), 5)))

        # Writing total number of molecules by species
        out_stream.write(blkSepar.format('Info for total number of molecules'))
        out_stream.write('{0:>11d}{1:>11d}\n'.format(1, 1))  # Currentely one polymer molecule in the simulation
        for i in range(1, len(contNfo)):
            out_stream.write('{0:>11d}{1:>11d}\n'.format(i + 1, 0))

        out_stream.write(blkSepar.format('Writing coordinates of all boxes'))
        # Writing coordinates of atoms in all boxes
        line_template = '{l[0]:>6} {l[1]:>13.8f} {l[2]:>13.8f} {l[3]:>13.8f}\n {l[4]:>12d} \n'
        for parts in sys.particles:
            line = ['',  0,  0,  0, 1]  # TODO: change the "1" to the actual box identifier
            try:
                line[0] = parts.type.name
                line[1] = parts.x
                line[2] = parts.y
                line[3] = parts.z
            except:
                continue
            out_stream.write(line_template.format(l=line))
        out_stream.close()

    def read_input(self, inp_file):
        tmp_dict = {}
        if os.path.isfile(inp_file):
            self.logger.info('Reading simulation parameters from {:} file'.format(inp_file))
            # Reading the cassandra .inp file as one long string
            inp_stream = open(inp_file, 'r')
            lines = inp_stream.read()

            raw_props = lines.split('#')

            for prop in raw_props:
                tmp = prop.split()
                if len(tmp) > 1:
                    tmp_dict[tmp[0]] = self.__parse_value__(tmp)

            # File seems fine let's close the stream and return true in the flag
            inp_stream.close()
            self.logger.info('Reading finished sucsessfully')
        else:
            self.logger.error('Cannot find specified file: ""{:}""'.format(inp_file))
        return tmp_dict

    def __parse_value__(self, cells):
        title = cells[0]
        if title == 'Run_Type':
            return OrderedDict([('type', cells[1]), ('steps', int(cells[2]))])

        elif title == 'Charge_Style':
            return OrderedDict([('type', cells[1]),
                                ('sum_type', cells[2]),
                                ('cut_val', float(cells[3])),
                                ('accuracy', float(cells[4]))])

        elif title == 'VDW_Style':
            return OrderedDict([('type', cells[1]),
                                ('cut_type', cells[2]),
                                ('cut_val', float(cells[3]))])

        elif title == 'Simulation_Length_Info':
            tmp = OrderedDict([('units', cells[2]),
                               ('prop_freq', int(cells[4])),
                               ('coord_freq', int(cells[6])),
                               ('run', int(cells[8]))])
            if len(cells) > 10:
                tmp['steps_per_sweep'] = int(cells[10])
                if len(cells) > 12:
                    tmp['block_averages'] = int(cells[12])
            return tmp

        elif title == 'CBMC_Info':
            return OrderedDict([('kappa_ins', int(cells[2])),
                                ('kappa_dih', int(cells[4])),
                                ('rcut_cbmc', float(cells[6]))])

        elif title == 'Box_Info':
            size = float(cells[3])
            if len(cells) > 6:
                size = [float(cells[3]), float(cells[4]), float(cells[5])]
            return OrderedDict([('box_count', int(cells[1])), ('box_type', cells[2]), ('box_size', size)])

        elif title == 'Prob_Translation':
            vals = []
            for i in range(2, len(cells) - 1):
                vals.append(float(cells[i]))
            return OrderedDict([('tot_prob', float(cells[1])),
                                ('limit_vals', vals)])

        elif title == 'Prob_Insertion':
            vals = []
            for i in range(2, len(cells) - 1):
                vals.append(cells[i])
            return OrderedDict([('tot_prob', float(cells[1])),
                                ('types', vals)])

        elif (title == 'Molecule_Files') or (title == 'Fragment_Files'):
            tmp = OrderedDict()
            for i in range(1, len(cells) - 2, 2):
                tmp['file' + str(i)] = [cells[i], int(cells[i + 1])]
            return tmp

        elif title == 'Start_Type':
            if cells[1] == 'read_config':
                specs = []
                for i in range(2, len(cells) - 3):
                    specs.append(int(cells[i]))
                return OrderedDict([('start_type', 'read_config'),
                                    ('species', specs),
                                    ('file_name', cells[len(cells) - 3])])

            if cells[1] == 'make_config':
                self.logger.error('Sorry, ''make_config'' regime  of ''Start_Type option is not supported yet'' ')
                exit(0)

            if cells[1] == 'add to config':
                self.logger.error('Sorry, ''add to config'' regime  of ''Start_Type option is not supported yet'' ')
                exit(0)

            if cells[1] == 'checkpoint':
                self.logger.error('Sorry, ''checkpoint'' regime  of ''Start_Type option is not supported yet'' ')
                exit(0)

        elif title == 'Property_Info':
            if int(cells[1]) == 1:
                tmp = OrderedDict()
                for i in range(2, len(cells) - 2):
                    tmp['prop' + str(i - 1)] = str.lower(cells[i])
                return tmp

        elif title == 'Seed_Info':
            return [int(cells[1]), int(cells[2])]

        elif (title == 'Prob_Deletion') or (title == 'Rcutoff_Low') or \
             (title == 'Bond_Prob_Cutoff') or (title == 'Chemical_Potential_Info'):
            return float(cells[1])

        elif (title == 'Average_Info') or (title == 'Nbr_Species') or (title == 'Temperature_Info'):
            return int(cells[1])

        else:
            return cells[1]


class McfWriter(object):
    # Static section names in MCF file
    mcf_tags = ['# Atom_Info', '# Bond_Info', '# Angle_Info', '# Dihedral_Info',
                '# Improper_Info', '# Intra_Scaling', '# Fragment_Info', '# Fragment_Connectivity']

    def __init__(self, psm_syst, file_ref, what_to_write='all'):
        self.out_stream = None
        self.empty_line = '0'
        self.syst = psm_syst
        self.file_ref = file_ref
        self.tags_to_write = self.__to_tags__(what_to_write)

    def write(self):
        # Initializing output stream

        with open(self.file_ref, 'w+') as out_stream:
            for (ttwr, ind) in zip(self.tags_to_write, range(len(self.mcf_tags))):
                if ttwr:
                    method = getattr(self, '__write_' + str.lower(self.mcf_tags[ind][2:]) + '__')
                    method(out_stream)
                else:
                    self.__write_empty__(out_stream, ind)
            out_stream.write('\nEND')
            out_stream.close()

    def __write_empty__(self, out_stream, ind):
        out_stream.write('{0:}\n{1:}\n\n'.format(self.mcf_tags[ind], self.empty_line))

    def __write_atom_info__(self, out):
        global kcalMol2K
        if out:
            # writing section header
            out.write('{:}\n'.format(self.mcf_tags[0]))
            # Verify and fix net system charge
            self.syst.zero_charge()
            # writing total number of particles
            out.write('{0:<6}\n'.format(self.syst.particles.count))
            count = 0
            line_template = '{l[0]:<6}{l[1]:<7}{l[2]:<5}{l[3]:<8.3f}{l[4]:<10.6f}' \
                            '{l[5]:<6}{l[6]:<11.3f}{l[7]:<9.3f}\n'
            if self.syst.particles.count > 0:
                for item in self.syst.particles:
                    line = [count + 1, '', '', '', 0, 'LJ', 0, 0]
                    if hasattr(item, 'charge'):
                        line[4] = item.charge
                    else:
                        line[4] = 0
                    if hasattr(item, 'type'):
                        if hasattr(item.type, 'name'):
                            line[1] = item.type.name
                        if hasattr(item.type, 'elem'):
                            line[2] = item.type.elem
                        if hasattr(item.type, 'mass'):
                            line[3] = item.type.mass
                        if hasattr(item.type, 'epsilon'):
                            line[6] = kcalMol2K * item.type.epsilon
                            line[7] = item.type.sigma
                    else:
                        continue
                    out.write(line_template.format(l=line))
                    count += 1
            out.write('\n')

    def __write_bond_info__(self, out):
        # writing section header
        out.write('{:}\n'.format(self.mcf_tags[1]))
        # writing total number of bonds
        out.write('{0:<6}\n'.format(self.syst.bonds.count))
        line_template = '{l[0]:<6d}{l[1]:<6d}{l[2]:<6d}{l[3]:<9}{l[4]:<6.3f}\n'
        count = 1
        for bond in self.syst.bonds:
            tmp = bond.type.k
            if tmp > 1000:
                tmp = 'fixed'
            line = [count, bond.a.tag, bond.b.tag, tmp, bond.type.r0]
            count += 1
            out.write(line_template.format(l=line))
        out.write('\n')

    def __write_angle_info__(self, out):
        # writing section header
        out.write('{:}\n'.format(self.mcf_tags[2]))
        # writing total number of angles
        out.write('{0:<6}\n'.format(self.syst.angles.count))
        line_template = '{l[0]:<6d}{l[1]:<6d}{l[2]:<6d}{l[3]:<6d}{l[4]:<12}{l[5]:<6.3f}\n'
        count = 1
        for angle in self.syst.angles:
            tmp = angle.type.k
            if tmp > 1000:
                tmp = 'fixed'
            line = [count, angle.a.tag, angle.b.tag, angle.c.tag, tmp, angle.type.theta0]
            count += 1
            out.write(line_template.format(l=line))
        out.write('\n')

    def __write_intra_scaling__(self, out):
        # writing section header
        out.write('{:}\n'.format(self.mcf_tags[5]))
        # writing vdW scaling:  1-2 1-3 1-4 1-N
        out.write('{:<5.1f}{:<5.1f}{:<8.4f}{:<4.1f}\n'.format(0, 0, 0, 1))
        # writing charge scaling:  1-2 1-3 1-4 1-N
        out.write('{:<5.1f}{:<5.1f}{:<8.4f}{:<4.1f}\n\n'.format(0, 0, 0, 1))

    def __write_dihidral_info__(self, out):
        print('Not supported yet')

    def __write_improper_info__(self, out):
        print('Not supported yet')

    def __write_fragment_info__(self, out):
        # TODO: Temporary implementation for one fragment
        # writing section header
        out.write('{:}\n'.format(self.mcf_tags[6]))
        # writing indexing
        out.write('{:}\n'.format(1))
        n = len(self.syst.particles)
        out.write('  '.join('{}'.format(item) for item in [1, n] + range(1, n + 1)))
        out.write('\n\n')

    def __write_fragment_connectivity__(self, out):
        print('Not supported yet')

    def __to_tags__(self, inpt):
        idxs = [False] * 8
        if inpt == 'all':
            idxs = [True] * 8
        elif inpt == 'atoms':
            idxs[0] = True
        elif isinstance(inpt, types.ListType) and (len(inpt) == 8):
            idxs = inpt
        return idxs

