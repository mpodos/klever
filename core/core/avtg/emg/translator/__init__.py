import os
import copy
import abc

from core.avtg.emg.common.signature import import_signature
from core.avtg.emg.common.code import FunctionDefinition, Aspect, Variable
from core.avtg.emg.common.interface import Container, Callback
from core.avtg.emg.translator.fsa import Automaton
from core.avtg.emg.common.process import Receive, Dispatch, Call, CallRetval, Condition, Subprocess, \
    get_common_parameter


class AbstractTranslator(metaclass=abc.ABCMeta):

    CF_PREFIX = 'ldv_control_function_'

    def __init__(self, logger, conf, avt, aspect_lines=None):
        self.logger = logger
        self.conf = conf
        self.task = avt
        self.files = {}
        self.aspects = {}
        self.entry_file = None
        self.model_aspects = []
        self._callback_fsa = []
        self._structures = {}
        self._model_fsa = []
        self._entry_fsa = None
        self._nested_automata = False
        self._omit_all_states = False
        self.__dump_automata = False
        self._omit_states = {
            'callback': True,
            'dispatch': True,
            'receive': True,
            'return value': False,
            'subprocess': False,
            'condition': False,
        }
        self.__identifier_cnt = -1
        self.__instance_modifier = 1
        self.__max_instances = None
        self.__reduce_container_aliases = True
        self.__combine_containers = True

        # Read translation options
        if "dump automata graphs" in self.conf["translation options"]:
            self.__dump_automata = self.conf["translation options"]["dump automata graphs"]
        if "translation options" not in self.conf:
            self.conf["translation options"] = {}
        if "max instances number" in self.conf["translation options"]:
            self.__max_instances = int(self.conf["translation options"]["max instances number"])
        if "instance modifier" in self.conf["translation options"]:
            self.__instance_modifier = self.conf["translation options"]["instance modifier"]
        if "combine containers" in self.conf["translation options"]:
            self.__combine_containers = self.conf["translation options"]["combine containers"]
        if "reduce container aliases" in self.conf["translation options"]:
            self.__reduce_container_aliases = self.conf["translation options"]["reduce container aliases"]
        if "pointer initialization" not in self.conf["translation options"]:
            self.conf["translation options"]["pointer initialization"] = {}
        if "pointer free" not in self.conf["translation options"]:
            self.conf["translation options"]["pointer free"] = {}
        for tag in ['structures', 'arrays', 'unions', 'primitives', 'enums', 'functions']:
            if tag not in self.conf["translation options"]["pointer initialization"]:
                self.conf["translation options"]["pointer initialization"][tag] = False
            if tag not in self.conf["translation options"]["pointer free"]:
                self.conf["translation options"]["pointer free"][tag] = \
                    self.conf["translation options"]["pointer initialization"][tag]
        if 'omit all states' in self.conf['translation options']:
            self._omit_all_states = self.conf['translation options']['omit all states']
        if "omit states" in self.conf["translation options"]:
            self._omit_states = self.conf["translation options"]["omit states"]
        if not self._omit_states:
            if 'omit composition' in self.conf["translation options"]:
                for tag in self._omit_states:
                    if tag in self.conf["translation options"]['omit composition']:
                        self._omit_states[tag] = self.conf["translation options"]['omit composition'][tag]
        if "nested automata" in self.conf["translation options"]:
            self._nested_automata = self.conf["translation options"]["nested automata"]
        if "direct control function calls" in self.conf["translation options"]:
            self._direct_cf_calls = self.conf["translation options"]["direct control function calls"]

        self.jump_types = set()
        if self._omit_states['callback']:
            self.jump_types.add(Call)
        if self._omit_states['dispatch']:
            self.jump_types.add(Dispatch)
        if self._omit_states['receive']:
            self.jump_types.add(Receive)
        if self._omit_states['return value']:
            self.jump_types.add(CallRetval)
        if self._omit_states['subprocess']:
            self.jump_types.add(Subprocess)
        if self._omit_states['condition']:
            self.jump_types.add(Condition)

        if not aspect_lines:
            self.additional_aspects = []
        else:
            self.additional_aspects = aspect_lines

    def translate(self, analysis, model):
        # Determine entry point name and file
        self.logger.info("Determine entry point name and file")
        self.__determine_entry(analysis)
        self.logger.info("Going to generate entry point function {} in file {}".
                         format(self.entry_point_name, self.entry_file))

        # Prepare entry point function
        self.logger.info("Generate C code from an intermediate model")
        self._prepare_code(analysis, model)

        # Print aspect text
        self.logger.info("Add individual aspect files to the abstract verification task")
        self.__generate_aspects()

        # Add aspects to abstract task
        self.logger.info("Add individual aspect files to the abstract verification task")
        self.__add_aspects()

        # Set entry point function in abstract task
        self.logger.info("Add entry point function to abstract verification task")
        self.__add_entry_points()

        self.logger.info("Model translation is finished")

    def _prepare_code(self, analysis, model):
        # Determine how many instances is required for a model
        self.logger.info("Determine how many instances is required to add to an environment model for each process")
        for process in model.event_processes:
            base_list = self._initial_instances(analysis, process)
            base_list = self._instanciate_processes(analysis, base_list, process)
            self.logger.info("Generate {} FSA instances for process {} with category {}".
                             format(len(base_list), process.name, process.category))

            for instance in base_list:
                fsa = Automaton(self.logger, analysis, instance, self.__yeild_identifier())
                self._callback_fsa.append(fsa)

        # Generate automata for models
        for process in model.model_processes:
            self.logger.info("Generate FSA for kernel model process {}".format(process.name))
            processes = self._instanciate_processes(analysis, [process], process)
            for instance in processes:
                fsa = Automaton(self.logger, analysis, instance, self.__yeild_identifier())
                self._model_fsa.append(fsa)

        # Generate state machine for init an exit
        # todo: multimodule automaton (issues #6563, #6571, #6558)
        self.logger.info("Generate FSA for module initialization and exit functions")
        self._entry_fsa = Automaton(self.logger, analysis, model.entry_process, self.__yeild_identifier())

        # Generates base code blocks
        for automaton in self._callback_fsa + self._model_fsa + [self._entry_fsa]:
            for state in list(automaton.fsa.states):
                automaton.generate_code(analysis, model, self, state)

        # Save digraphs
        if self.__dump_automata:
            automaton_dir = "automaton"
            self.logger.info("Save automata to directory {}".format(automaton_dir))
            os.mkdir(automaton_dir)
            for automaton in self._callback_fsa + self._model_fsa + [self._entry_fsa]:
                automaton.save_digraph(automaton_dir)

        # Generate control functions
        self.logger.info("Generate control functions for each automaton")
        self._generate_control_functions(analysis, model)

        # Add structures to declare types
        self.files[self.entry_file]['types'] = sorted(list(set(self._structures.values())), key=lambda v: v.identifier)

    def _select_containers_implementations(self, analysis, process):
        relevant_multi_containers = {}
        accesses = process.accesses()
        self.logger.debug("Calculate relevant containers with several implementations for process {} for category {}".
                          format(process.name, process.category))
        for access in [accesses[name] for name in sorted(accesses.keys())]:
            for inst_access in [inst for inst in sorted(access, key=lambda i: i.expression) if inst.interface]:
                if type(inst_access.interface) is Container and \
                                len(analysis.implementations(inst_access.interface)) > 1 and \
                                inst_access.interface.identifier not in relevant_multi_containers:
                    implementations = analysis.implementations(inst_access.interface)
                    relevant_multi_containers[inst_access.interface.identifier] = implementations
                elif len(inst_access.complete_list_interface) > 1:
                    impl_cnt = [intf for intf in inst_access.complete_list_interface if type(intf) is Container and
                                len(analysis.implementations(intf)) > 1]
                    if len(impl_cnt) > 0:
                        interface = impl_cnt[0]
                        implementations = analysis.implementations(interface)
                        relevant_multi_containers[interface.identifier] = implementations

        if self.__reduce_container_aliases:
            for interface in [analysis.interfaces[key] for key in relevant_multi_containers]:
                # Collect allback implementations
                implementations = relevant_multi_containers[interface.identifier]
                values = [i.value for i in implementations]
                child_implementations = {val: set() for val in values}
                intfs = set()
                for intf in [intf for intf in interface.field_interfaces.values() if type(intf) is Callback]:
                    for ci in intf.declaration.implementations.values():
                        if ci.base_value in values:
                            child_implementations[ci.base_value].add(ci.value)
                            intfs.add(ci.value)

                if len(intfs) > 0:
                    # Greedy choose containers which cover all callback implementations
                    values = reversed(sorted(values, key=lambda val: len(child_implementations[val])))
                    selected_containers = set()
                    covered = set()
                    for intf in intfs:
                        if intf not in covered:
                            for value in values:
                                if intf in child_implementations[value]:
                                    selected_containers.add(value)
                                    covered.update(child_implementations[value])
                                    break
                    new_implementations = [i for i in implementations if i.value in sorted(selected_containers)]
                    self.logger.debug('Having {} instances choose {} of them for interface {}'.
                                      format(len(implementations), len(new_implementations), interface.identifier))
                    relevant_multi_containers[interface.identifier] = new_implementations

        return relevant_multi_containers

    def _instanciate_processes(self, analysis, instances, process):
        base_list = instances

        # Copy base instances for each known implementation
        relevant_multi_containers = self._select_containers_implementations(analysis, process)

        # Copy instances for each implementation of a container
        if len(list(relevant_multi_containers.keys())) > 0:
            self.logger.debug(
                "Found {} relevant containers with several implementations for process {} for category {}".
                format(str(len(relevant_multi_containers)), process.name, process.category))

            if not self.__combine_containers:
                for interface in sorted(list(relevant_multi_containers.keys())):
                    new_base_list = []
                    implementations = relevant_multi_containers[interface]

                    for implementation in implementations:
                        for instance in base_list:
                            newp = self._copy_process(instance)
                            newp.forbide_except(analysis, interface, implementation)
                            new_base_list.append(newp)

                    base_list = list(new_base_list)
            else:
                # Generate ranking by implementations number
                sorted_cnts = list(reversed(sorted(list(relevant_multi_containers.keys()),
                                                   key=lambda c: len(relevant_multi_containers[c]))))
                ivector = [0 for i in range(len(sorted_cnts))]

                groups = []
                new_base_list = []
                for gr_id in range(len(relevant_multi_containers[sorted_cnts[0]])):
                    group = {}
                    for cnt in range(len(sorted_cnts)):
                        group[sorted_cnts[cnt]] = relevant_multi_containers[sorted_cnts[cnt]][ivector[cnt]]
                        ivector[cnt] += 1
                        if ivector[cnt] == len(relevant_multi_containers[sorted_cnts[cnt]]):
                            ivector[cnt] = 0
                    groups.append(group)

                for group in groups:
                    for instance in base_list:
                        newp = self._copy_process(instance)
                        for identifier in group:
                            interface = analysis.interfaces[identifier]
                            newp.forbide_except(analysis, interface, group[identifier])
                        new_base_list.append(newp)

                base_list = list(new_base_list)

        new_base_list = []
        for instance in base_list:
            # Copy callbacks or resources which are not tied to a container
            accesses = instance.accesses()
            relevant_multi_leafs = set()
            for access in [accesses[name] for name in sorted(accesses.keys())]:
                relevant_multi_leafs.update([inst for inst in access if inst.interface and
                                             type(inst.interface) is Callback and
                                             len(instance.get_implementations(analysis, inst)) > 1])

            if len(relevant_multi_leafs) > 0:
                self.logger.debug("Found {} accesses with several implementations for process {} for category {}".
                                  format(len(relevant_multi_leafs), process.name, process.category))
                for access in sorted(list(relevant_multi_leafs), key=lambda intf: intf.expression):
                    for implementation in analysis.implementations(access.interface):
                        newp = self._copy_process(instance)
                        newp.forbide_except(analysis, implementation)
                        new_base_list.append(newp)
            else:
                new_base_list.append(instance)

        base_list = new_base_list
        return base_list

    def _initial_instances(self, analysis, process):
        base_list = []
        undefined_labels = []
        # Determine nonimplemented containers
        self.logger.debug("Calculate number of not implemented labels and collateral values for process {} with "
                          "category {}".format(process.name, process.category))
        for label in [process.labels[name] for name in sorted(process.labels.keys())
                      if len(process.labels[name].interfaces) > 0]:
            nonimplemented_intrerfaces = [interface for interface in label.interfaces
                                          if len(analysis.implementations(analysis.interfaces[interface])) == 0]
            if len(nonimplemented_intrerfaces) > 0:
                undefined_labels.append(label)

        # Determine is it necessary to make several instances
        if len(undefined_labels) > 0:
            for i in range(self.__instance_modifier):
                base_list.append(self._copy_process(process))
        else:
            base_list.append(self._copy_process(process))

        self.logger.info("Prepare {} instances for {} undefined labels of process {} with category {}".
                         format(len(base_list), len(undefined_labels), process.name, process.category))

        return base_list

    def extract_relevant_automata(self, automata_peers, peers, sb_type=None):
        for peer in peers:
            relevant_automata = [automaton for automaton in self._callback_fsa + self._model_fsa + [self._entry_fsa]
                                 if automaton.process.name == peer["process"].name]
            for automaton in relevant_automata:
                if automaton.identifier not in automata_peers:
                    automata_peers[automaton.identifier] = {
                        "automaton": automaton,
                        "states": set()
                    }
                for state in [node for node in automaton.fsa.states
                              if node.action and node.action.name == peer["subprocess"].name]:
                    if not sb_type or isinstance(state.action, sb_type):
                        automata_peers[automaton.identifier]["states"].add(state)

    def registration_intf_check(self, analysis, model, function_call):
        automata_peers = {}

        name = analysis.callback_name(function_call)
        if name:
            # Caclulate relevant models
            if name in analysis.modules_functions:
                relevant_models = analysis.collect_relevant_models(name)

                # Get list of models
                process_models = [model for model in model.model_processes if model.name in relevant_models]

                # Check relevant state machines for each model
                for model in process_models:
                    signals = [model.actions[name] for name in sorted(model.actions.keys())
                               if (type(model.actions[name]) is Receive or
                                   type(model.actions[name]) is Dispatch) and
                               len(model.actions[name].peers) > 0]

                    # Get all peers in total
                    peers = []
                    for signal in signals:
                        peers.extend(signal.peers)

                    # Add relevant state machines
                    self.extract_relevant_automata(automata_peers, peers)
        else:
            self.logger.warning("Cannot find module function for callback '{}'".format(function_call))

        return automata_peers

    def _copy_process(self, process):
        inst = copy.copy(process)
        if self.__max_instances == 0:
            raise RuntimeError('EMG tries to generate more instances than it is allowed by configuration ({})'.
                               format(int(self.conf["translation options"]["max instances number"])))
        elif self.__max_instances:
            self.__max_instances -= 1

        inst.forbidded_implementations = set(process.forbidded_implementations)
        return inst

    def __yeild_identifier(self):
        self.__identifier_cnt += 1
        return self.__identifier_cnt

    def __determine_entry(self, analysis):
        if len(analysis.inits) == 1:
            file = list(analysis.inits.keys())[0]
            self.logger.info("Choose file {} to add an entry point function".format(file))
            self.entry_file = file
        elif len(analysis.inits) < 1:
            raise RuntimeError("Cannot generate entry point without module initialization function")

        if "entry point" in self.conf:
            self.entry_point_name = self.conf["entry point"]
        else:
            self.entry_point_name = "main"

    def _add_function_definition(self, file, function):
        if file not in self.files:
            self.files[file] = {
                'variables': {},
                'functions': {}
            }

        self.files[file]['functions'][function.name] = function

    def _add_global_variable(self, variable):
        if variable.file:
            file = variable.file
        else:
            file = self.entry_file

        if file not in self.files:
            self.files[file] = {
                'variables': {},
                'functions': {}
            }

        self.files[file]['variables'][variable.name] = variable

    def _generate_control_functions(self, analysis, model):
        global_switch_automata = []

        if self._omit_all_states and not self._nested_automata:
            raise NotImplementedError('EMG options are inconsistent: cannot create label-based automata without nested'
                                      'dispatches')

        # Generate automata control function
        self.logger.info("Generate control functions for the environment model")
        if self._omit_all_states:
            for automaton in sorted(list(self._callback_fsa), key=lambda fsa: fsa.identifier):
                func = self._label_cfunction(analysis, automaton)
            if func and not self._nested_automata:
                global_switch_automata.append(func)
        else:
            self.logger.info("Generate control functions for the environment model")
            for automaton in sorted(list(self._callback_fsa) + [self._entry_fsa], key=lambda fsa: fsa.identifier):
                automaton.state_blocks = self._state_sequences(automaton)

            for automaton in sorted(list(self._callback_fsa), key=lambda fsa: fsa.identifier):
                func = self._state_cfunction(analysis, automaton)
                if func and not self._nested_automata:
                    global_switch_automata.append(func)

        if self._omit_all_states:
            func = self._label_cfunction(analysis, self._entry_fsa)
        else:
            func = self._state_cfunction(analysis, self._entry_fsa)
        if func:
            global_switch_automata.append(func)
        else:
            raise ValueError('Entry point function must contain init/exit automaton control function')

        # Generate model control function
        for name in (pr.name for pr in model.model_processes):
            for automaton in [a for a in sorted(list(self._model_fsa), key=lambda fsa: fsa.identifier)
                              if a.process.name == name]:
                self._label_cfunction(analysis, automaton, name)

        # Generate entry point function
        func = self._generate_entry_functions(global_switch_automata)
        self._add_function_definition(self.entry_file, func)

    def _generate_entry_functions(self, global_switch_automata):
        self.logger.info("Finally generate entry point function {}".format(self.entry_point_name))
        # FunctionDefinition prototype
        ep = FunctionDefinition(
            self.entry_point_name,
            self.entry_file,
            "void {}(void)".format(self.entry_point_name),
            False
        )

        body = [
            "ldv_initialize();",
            ""
            "while(1) {",
            "\tswitch(ldv_undef_int()) {"
        ]

        for index in range(len(global_switch_automata)):
            cfunction = global_switch_automata[index]
            body.extend(
                [
                    "\t\tcase {}: ".format(index),
                    "\t\t\t{}(0);".format(cfunction.name),
                    "\t\tbreak;"
                ]
            )
        body.extend(
            [
                "\t\tdefault: break;",
                "\t}",
                "}"
            ]
        )
        ep.body.concatenate(body)

        return ep

    def _call(self, automaton, state):
        # Generate function call and corresponding function
        fname = "ldv_{}_{}_{}_{}".format(automaton.process.name, state.action.name, automaton.identifier,
                                         state.identifier)
        if 'invoke' not in state.code:
            return state.code['body']

        # Generate special function with call
        if 'retval' in state.code:
            ret = state.code['callback'].points.return_value.to_string('')
            ret_expr = 'return '
            cf_ret_expr = state.code['retval'] + ' = '
        else:
            ret = 'void'
            ret_expr = ''
            cf_ret_expr = ''

        resources = [state.code['callback'].to_string('arg0')]
        params = []
        for index in range(len(state.code['callback'].points.parameters)):
            if index in state.code["pointer parameters"]:
                resources.append(state.code['callback'].points.parameters[index].take_pointer.
                                 to_string('arg{}'.format(index + 1)))
                params.append('*arg{}'.format(index + 1))
            else:
                resources.append(state.code['callback'].points.parameters[index].
                                 to_string('arg{}'.format(index + 1)))
                params.append('arg{}'.format(index + 1))
        params = ", ".join(params)
        resources = ", ".join(resources)

        function = FunctionDefinition(fname,
                                      state.code['file'],
                                      "{} {}({})".format(ret, fname, resources),
                                      True)

        function.body.concatenate("/* Callback {} */".format(state.action.name))
        inv = [
            '/* Callback {} */'.format(state.action.name)
        ]

        if state.code['check pointer']:
            f_invoke = cf_ret_expr + fname + '(' + ', '.join([state.code['invoke']] + state.code['parameters']) + ');'
            inv.append('if ({})'.format(state.code['invoke']))
            inv.append('\t' + f_invoke)
            call = ret_expr + '(*arg0)' + '(' + params + ')'
        else:
            f_invoke = cf_ret_expr + fname + '(' + \
                       ', '.join([state.code['variable']] + state.code['parameters']) + ');'
            inv.append(f_invoke)
            call = ret_expr + '({})'.format(state.code['invoke']) + '(' + params + ')'
        function.body.concatenate('{};'.format(call))

        self._add_function_definition(state.code['file'], function)

        if 'pre_call' in state.code and len(state.code['pre_call']) > 0:
            inv = state.code['pre_call'] + inv
        if 'post_call' in state.code and len(state.code['post_call']) > 0:
            inv.extend(state.code['post_call'])

        return inv

    def _relevant_checks(self, state):
        checks = []

        # Add state checks
        if state.code and 'relevant automata' in state.code:
            for name in sorted(state.code['relevant automata'].keys()):
                for st in state.code['relevant automata'][name]['states']:
                    for index in state.code['relevant automata'][name]["automaton"].state_blocks:
                        if st in state.code['relevant automata'][name]["automaton"].state_blocks[index]:
                            checks.append("{} == {}".
                                          format(state.code['relevant automata'][name]["automaton"].state_variable.name,
                                                 index))

        return checks

    def _call_cf(self, automaton, parameter='0'):
        sv = automaton.thread_variable

        if self._direct_cf_calls:
            return '{}({});'.format(self.CF_PREFIX + str(automaton.identifier), parameter)
        else:
            return 'ldv_thread_create({}, {}, {});'.format('& ' + sv.name,
                                                           self.CF_PREFIX + str(automaton.identifier),
                                                           parameter)

    def join_cf(self, automaton):
        sv = automaton.thread_variable

        return 'ldv_thread_join({});'.format('& ' + sv.name)

    def _get_cf_struct(self, automaton, params):
        cache_identifier = ''
        for param in params:
            cache_identifier += param.identifier

        if cache_identifier not in self._structures:
            struct_name = 'ldv_struct_{}_{}'.format(automaton.process.name, automaton.identifier)
            if struct_name in self._structures:
                raise KeyError('Structure name is not unique')

            decl = import_signature('struct {} a'.format(struct_name))
            for index in range(len(params)):
                decl.fields['arg{}'.format(index)] = params[index]
            decl.fields['signal_pending'] = import_signature('int a')

            self._structures[cache_identifier] = decl
        else:
            decl = self._structures[cache_identifier]

        return decl

    def _dispatch(self, analysis, automaton, state):
        body = []
        if not self._direct_cf_calls:
            body = ['int ret;']

        if len(state.code['relevant automata']) == 0:
            return ['/* Skip dispatch {} without processes to receive */'.format(state.action.name)]

        # Check dispatch type
        replicative = False
        for name in state.code['relevant automata']:
            for st in state.code['relevant automata'][name]['states']:
                if st.action.replicative:
                    replicative = True
                    break

        # Determine parameters
        df_parameters = []
        function_parameters = []

        # Add parameters
        for index in range(len(state.action.parameters)):
            # Determine dispatcher parameter
            interface = get_common_parameter(state.action, automaton.process, index)

            # Determine dispatcher parameter
            dispatcher_access = automaton.process.resolve_access(state.action.parameters[index],
                                                                 interface.identifier)
            variable = automaton.determine_variable(analysis, dispatcher_access.label, interface.identifier)
            dispatcher_expr = dispatcher_access.access_with_variable(variable)

            function_parameters.append(variable.declaration)
            df_parameters.append(dispatcher_expr)

        decl = self._get_cf_struct(automaton, function_parameters)
        cf_param = '& cf_arg'

        vf_param_var = Variable('cf_arg', None, decl, False)
        body.append(vf_param_var.declare() + ';')

        for index in range(len(function_parameters)):
            body.append('{}.arg{} = arg{};'.format(vf_param_var.name, index, index))

        if not self._nested_automata:
            vf_param_var = self._dispatch_var(automaton, state, function_parameters)
            body.append('{} = {};'.format(vf_param_var.name, cf_param))
        body.append('')

        blocks = []
        if self._nested_automata:
            if replicative:
                for name in state.code['relevant automata']:
                    for r_state in state.code['relevant automata'][name]['states']:
                        block = []
                        call = self._call_cf(state.code['relevant automata'][name]['automaton'], cf_param)
                        if r_state.action.replicative:
                            if self._direct_cf_calls:
                                block.append(call)
                            else:
                                block.append('ret = {}'.format(call))
                                block.append('ldv_assume(ret == 0);')
                            blocks.append(block)
                            break
                        else:
                            self.logger.warning(
                                'Cannot generate dispatch based on labels for receive {} in process {} with category {}'
                                    .format(r_state.action.name,
                                            state.code['relevant automata'][name]['automaton'].process.name,
                                            state.code['relevant automata'][name]['automaton'].process.category))
            else:
                for name in state.code['relevant automata']:
                    call = self.join_cf(state.code['relevant automata'][name]['automaton'])
                    if self._direct_cf_calls:
                        block = [call]
                    else:
                        block = ['ret = {}'.format(call),
                                 'ldv_assume(ret == 0);']
                    blocks.append(block)
        else:
             blocks.append(
                 [
                     '{}->signal_pending = 1;'.format(vf_param_var.name)
                 ]
             )

        if state.action.broadcast:
            for block in blocks:
                body.extend(block)
        else:
            if len(blocks) == 1:
                body.extend(blocks[0])
            elif len(blocks) == 2:
                for index in range(2):
                    if index == 0:
                        body.append('if (ldv_undef_int()) {')
                    else:
                        body.append('else {')
                    body.extend(['\t' + stm for stm in blocks[index]])
                    body.append('}')
            else:
                body.append('switch (ldv_undef_int()) {')
                for index in range(len(blocks)):
                    body.append('\tcase {}: '.format(index) + '{')
                    body.extend(['\t\t' + stm for stm in blocks[index]])
                    body.append('\t\tbreak;')
                    body.append('\t};')
                body.append('\tdefault: ldv_stop();')
                body.append('};')
        body.append('return;')

        if len(function_parameters) > 0:
            df = FunctionDefinition(
                "ldv_dispatch_{}_{}_{}".format(automaton.identifier, state.identifier, state.action.name),
                self.entry_file,
                "void f({})".format(', '.join([function_parameters[index].to_string('arg{}'.format(index)) for index in
                                               range(len(function_parameters))])),
                False
            )
        else:
            df = FunctionDefinition(
                "ldv_dispatch_{}_{}_{}".format(automaton.identifier, state.identifier, state.action.name),
                self.entry_file,
                "void f(void)",
                False
            )

        df.body.concatenate(body)
        automaton.functions.append(df)
        self._add_function_definition(self.entry_file, df)

        return [
            '/* Dispatch {} */'.format(state.action.name),
            '{}({});'.format(df.name, ', '.join(df_parameters))
        ]

    def _dispatch_var(self, automaton, state, params):
        decl = self._get_cf_struct(automaton, params)
        vf_param_var = Variable('ldv_dispatch_params_{}_{}'.format(automaton.identifier, state.identifier),
                                None, decl.take_pointer, False)

        if vf_param_var.name not in self.files[self.entry_file]['variables']:
            self._add_global_variable(vf_param_var)
        return vf_param_var

    def _action_base_block(self, analysis, automaton, state):
        block = []
        v_code = []

        if type(state.action) is Call:
            if not self._nested_automata:
                checks = self._relevant_checks(state)
                if len(checks) > 0:
                    block.append('ldv_assume({});'.format(' || '.join(checks)))

            call = self._call(automaton, state)
            block.extend(call)
        elif type(state.action) is CallRetval:
            block.append('/* Skip {} */'.format(state.desc['label']))
        elif type(state.action) is Condition:
            for stm in state.code['body']:
                block.append(stm)
        elif type(state.action) is Dispatch:
            if not self._nested_automata:
                checks = self._relevant_checks(state)
                if len(checks) > 0:
                    block.append('ldv_assume({});'.format(' || '.join(checks)))

            call = self._dispatch(analysis, automaton, state)

            block.extend(call)
        elif type(state.action) is Receive:
            param_declarations = []
            param_expressions = []

            if len(state.action.parameters) > 0:
                for index in range(len(state.action.parameters)):
                    # Determine dispatcher parameter
                    interface = get_common_parameter(state.action, automaton.process, index)

                    # Determine receiver parameter
                    receiver_access = automaton.process.resolve_access(state.action.parameters[index],
                                                                       interface.identifier)
                    var = automaton.determine_variable(analysis, receiver_access.label, interface.identifier)
                    receiver_expr = receiver_access.access_with_variable(var)

                    param_declarations.append(var.declaration)
                    param_expressions.append(receiver_expr)

            if self._nested_automata:
                if state.action.replicative:
                    if len(param_declarations) > 0:
                        decl = self._get_cf_struct(automaton, [val for val in param_declarations])
                        var = Variable('cf_arg_struct', None, decl.take_pointer, False)
                        v_code.append('/* Received labels */')
                        v_code.append('{} = ({}*) arg0;'.format(var.declare(), decl.to_string('')))
                        v_code.append('')

                        block.append('')
                        block.append('/* Assign recieved labels */')
                        block.append('if (cf_arg_struct) {')
                        for index in range(len(param_expressions)):
                            block.append('\t{} = cf_arg_struct->arg{};'.format(param_expressions[0], index))
                        block.append('}')
                else:
                    block.append('/* Skip {} */'.format(state.desc['label']))
            else:
                elements = []
                for name in state.code['relevant automata']:
                    for r_state in state.code['relevant automata'][name]['states']:
                        bl = []
                        dispatch_var = self._dispatch_var(state.code['relevant automata'][name]['automaton'], r_state,
                                                          param_declarations)

                        conditions = ['{}->signal_pending'.format(dispatch_var.name)]
                        if len(state.code["receive guard"]) > 0:
                            for condition in state.code["receive guard"]:
                                stm = condition
                                for position in range(1, len(param_expressions) + 1):
                                    stm = stm.replace('$ARG{}'.format(position), '{}->arg{}'.format(dispatch_var.name,
                                                                                                    position - 1))
                                conditions.append(stm)
                        bl.append('ldv_assume({});'.format(' && '.join(conditions)))
                        for index in range(len(param_expressions)):
                            bl.append('{} = {}->arg{};'.format(param_expressions[0], dispatch_var.name, index))
                        bl.append('{}->signal_pending = 0;'.format(dispatch_var.name))
                        elements.append(bl)

                if len(elements) == 1:
                    block = elements[0]
                elif len(elements) == 2:
                    first = True
                    for element in elements:
                        if first:
                            block.append('if (ldv_undef_int()) {')
                            first = False
                        else:
                            block.append('else {')
                        block.extend(['\t' + stm for stm in element])
                        block.append('}')
                elif len(elements) > 2:
                    block.append('switch (ldv_undef_int()) {')
                    for index in range(len(elements)):
                        block.append('\tcase {}:'.format(index) + '{')
                        block.extend(['\t\t' + stm for stm in elements[index]])
                        block.append('\t}')
                    block.append('\tdefault: ldv_stop();')
                    block.append('}')
                else:
                    block.append('/* Skip receive {} without dispatchers */'.format(state.desc['label']))
        elif type(state.action) is Subprocess:
            for stm in state.code['body']:
                block.append(stm)
        else:
            raise ValueError('Unexpected state action')

        return v_code, block

    def _label_sequence(self, analysis, automaton, initial_states, name):
        f_code = []
        v_code = []

        state_stack = []
        if len(initial_states) > 1:
            action = Condition(name)
            new = automaton.fsa.new_state(action)
            new.successors = initial_states
            cd = {
                'body': ['/* Artificial state */'],
                'guard': []
            }
            new.code = cd
            state_stack.append(new)
        else:
            state_stack.append(list(automaton.fsa.initial_states)[0])

        processed_states = set()
        conditional_stack = []
        tab = 0
        while len(state_stack) > 0:
            state = state_stack.pop()
            processed_states.add(state)

            if type(state.action) is Subprocess:
                code = [
                    '/* Jump to subprocess {} initial state */'.format(state.action.name),
                    'goto ldv_{}_{};'.format(state.action.name, automaton.identifier)
                ]
            else:
                new_v_code, code = self._action_base_block(analysis, automaton, state)
                v_code.extend(new_v_code)

            if len(conditional_stack) > 0 and conditional_stack[-1]['condition'] == 'switch' and \
                    state in conditional_stack[-1]['state'].successors:
                if conditional_stack[-1]['counter'] != 0:
                    f_code.append('\t' * tab + 'break;')
                    tab -= 1
                    f_code.append('\t' * tab + '}')
                f_code.append('\t' * tab + 'case {}: '.format(conditional_stack[-1]['counter']) + '{')
                conditional_stack[-1]['counter'] += 1
                conditional_stack[-1]['cases left'] -= 1
                tab += 1

                if state.code and len(state.code['guard']) > 0:
                    f_code.append('\t' * tab + 'if ({}) '.format(' && '.join(sorted(state.code['guard']))) + '{')
                    tab += 1
                    for stm in code:
                        f_code.append('\t' * tab + stm)
                    tab -= 1
                    f_code.append('\t' * tab + '}')
                    f_code.append('\t' * tab + 'else')
                    tab += 1
                    f_code.append('\t' * tab + 'ldv_stop();')
                    tab -= 1
                else:
                    for stm in code:
                        f_code.append('\t' * tab + stm)
            elif len(conditional_stack) > 0 and conditional_stack[-1]['condition'] == 'if' and \
                    (state in automaton.fsa.initial_states or state in conditional_stack[-1]['state'].successors):
                if conditional_stack[-1]['counter'] != 0:
                    tab -= 1
                    f_code.append('\t' * tab + '}')
                    f_code.append('\t' * tab + 'else {')
                    tab += 1

                conditional_stack[-1]['counter'] += 1
                conditional_stack[-1]['cases left'] -= 1

                if state.code and len(state.code['guard']) > 0:
                    f_code.append('\t' * tab + 'ldv_assume({});'.format(' && '.join(sorted(state.code['guard']))))
                for stm in code:
                    f_code.append('\t' * tab + stm)
            else:
                f_code.append('')

                if state.code and len(state.code['guard']) > 0:
                    f_code.append('\t' * tab + 'if ({}) '.format(' && '.join(state.code['guard'])) + '{')
                    tab += 1
                    for stm in code:
                        f_code.append('\t' * tab + stm)
                    tab -= 1
                    f_code.append('\t' * tab + '}')
                else:
                    for stm in code:
                        f_code.append('\t' * tab + stm)

            last_action = False
            closed_condition = True
            while closed_condition:
                last_action = False
                closed_condition = False
                if len(conditional_stack) > 0:
                    if len(state.successors) == 0:
                        last_action = True
                    elif type(state.action) is Subprocess:
                        last_action = True
                    else:
                        for succ in state.successors:
                            trivial_predecessors = len([p for p in succ.predecessors if type(p.action) is not Subprocess])
                            if trivial_predecessors > 1:
                                last_action = True
                                break

                    if last_action and conditional_stack[-1]['cases left'] == 0 and \
                            conditional_stack[-1]['condition'] == 'switch':
                        f_code.append('\t' * tab + 'break;')
                        tab -= 1
                        f_code.append('\t' * tab + '}')
                        f_code.append('\t' * tab + 'default: ldv_stop();')
                        tab -= 1
                        f_code.append('\t' * tab + '}')
                        conditional_stack.pop()
                        closed_condition = True
                    elif last_action and conditional_stack[-1]['cases left'] == 0 and \
                            conditional_stack[-1]['condition'] == 'if':
                        tab -= 1
                        f_code.append('\t' * tab + '}')
                        conditional_stack.pop()
                        closed_condition = True

            if (type(state.action) is not Subprocess or len(state.code['guard']) > 0) and \
                    (not last_action or (last_action and closed_condition)):
                if len(state.successors) == 1:
                    if list(state.successors)[0] not in state_stack and \
                                    list(state.successors)[0] not in processed_states:
                        state_stack.append(list(state.successors)[0])
                elif len(state.successors) > 1:
                    if_condition = None
                    if len(state.successors) == 2:
                        successors = sorted(list(state.successors), key=lambda f: f.identifier)
                        if_condition = 'ldv_undef_int()'

                    if if_condition:
                        for succ in successors:
                            state_stack.append(succ)

                        condition = {
                            'condition': 'if',
                            'state': state,
                            'cases left': 2,
                            'counter': 0
                        }

                        f_code.append('\t' * tab + 'if ({}) '.format(if_condition) + '{')
                        tab += 1

                        conditional_stack.append(condition)
                    else:
                        for succ in sorted(list(state.successors), key=lambda f: f.identifier):
                            state_stack.append(succ)

                        condition = {
                            'condition': 'switch',
                            'state': state,
                            'cases left': len(list(state.successors)),
                            'counter': 0
                        }

                        f_code.append('\t' * tab + 'switch (ldv_undef_int()) {')
                        tab += 1

                        conditional_stack.append(condition)

        return [v_code, f_code]

    def _label_cfunction(self, analysis, automaton, aspect=None):
        self.logger.info('Generate label-based control function for automaton {} based on process {} of category {}'.
                         format(automaton.identifier, automaton.process.name, automaton.process.category))
        v_code = []
        f_code = []

        # Check necessity to return a value
        if aspect and analysis.kernel_functions[aspect].declaration.return_value and \
                analysis.kernel_functions[aspect].declaration.return_value.identifier != 'void':
            ret_expression = 'return $res;'
        else:
            ret_expression = 'return;'

        # Generate function definition
        cf = self.__new_control_function(analysis, automaton, v_code, f_code, aspect)

        main_v_code, main_f_code = self._label_sequence(analysis, automaton, automaton.fsa.initial_states,
                                                        'initial_state')
        v_code.extend(main_v_code)
        f_code.extend(main_f_code)
        f_code.append(ret_expression)

        processed = []
        for subp in [s for s in sorted(automaton.fsa.states, key=lambda s: s.identifier)
                     if type(s.action) is Subprocess]:
            if subp.action.name not in processed:
                sp_v_code, sp_f_code = self._label_sequence(analysis, automaton, subp.successors,
                                                            '{}_initial_state'.format(subp.action.name))

                v_code.extend(sp_v_code)
                f_code.extend([
                    '',
                    '/* Sbprocess {} */'.format(subp.action.name),
                    'ldv_{}_{}:'.format(subp.action.name, automaton.identifier)
                ])
                f_code.extend(sp_f_code)
                f_code.append(ret_expression)
                processed.append(subp.action.name)

        if not self._nested_automata and not aspect:
            self._add_global_variable(automaton.state_variable)
        elif not aspect:
            self._add_global_variable(automaton.thread_variable)

        cf.body.concatenate(v_code + f_code)
        automaton.control_function = cf
        return cf

    def __new_control_function(self, analysis, automaton, v_code, f_code, aspect=None):
        # Function type
        if aspect:
            cf = Aspect(aspect, analysis.kernel_functions[aspect].declaration, 'around')
            self.model_aspects.append(cf)
        else:
            cf = FunctionDefinition(self.CF_PREFIX + str(automaton.identifier), self.entry_file, 'void f(void *cf_arg)',
                                    False)
            self._add_function_definition(self.entry_file, cf)

            if self._nested_automata:
                param_declarations = []
                param_expressions = []
                for receive in [r for r in automaton.process.actions.values() if type(r) is Receive and r.replicative]:
                    if len(receive.parameters) > 0:
                        for index in range(len(receive.parameters)):
                            # Determine dispatcher parameter
                            interface = get_common_parameter(receive, automaton.process, index)

                            # Determine receiver parameter
                            receiver_access = automaton.process.resolve_access(receive.parameters[index],
                                                                               interface.identifier)
                            var = automaton.determine_variable(analysis, receiver_access.label, interface.identifier)
                            receiver_expr = receiver_access.access_with_variable(var)

                            param_declarations.append(var.declaration)
                            param_expressions.append(receiver_expr)
                        break

        if self._nested_automata:
            for var in automaton.variables(analysis):
                definition = var.declare() + ";"
                v_code.append(definition)
        else:
            for var in automaton.variables(analysis):
                self._add_global_variable(var)

        return cf

    def _state_sequences(self, automaton):
        blocks_stack = sorted(list(automaton.fsa.initial_states), key=lambda f: f.identifier)
        blocks = {}
        while len(blocks_stack) > 0:
            origin = blocks_stack.pop()
            block = []
            state_stack = [origin]
            no_jump = True

            while len(state_stack) > 0:
                state = state_stack.pop()
                block.append(state)
                no_jump = (type(state.action) not in self.jump_types) and no_jump

                if len(state.successors) == 1 and (no_jump or list(state.successors)[0] not in self.jump_types):
                    state_stack.append(list(state.successors)[0])

            blocks[origin.identifier] = block

            for state in [st for st in sorted(list(state.successors), key=lambda f: f.identifier)
                          if st.identifier not in blocks and st not in blocks_stack]:
                blocks_stack.append(state)

        return blocks

    def _state_sequence_code(self, analysis, automaton, block):
        first = True
        code = []
        v_code = []

        for state in block:
            new_v_code, block = self._action_base_block(analysis, automaton, state)
            v_code.extend(new_v_code)

            if state.code and len(state.code['guard']) > 0 and first:
                code.append('ldv_assume({});'.format(
                    ' && '.join(sorted(['{} == {}'.format(automaton.state_variable.name, state.identifier)] +
                                       state.code['guard']))))
                code.extend(block)
                first = False
            elif state.code and len(state.code['guard']) > 0:
                code.append('if({}) '.format(
                    ' && '.join(sorted(['{} == {}'.format(automaton.state_variable.name, state.identifier)] +
                                       state.code['guard']))) + '{')
                for st in block:
                    code.append('\t' + st)
                code.append('}')
            else:
                code.extend(block)
            code.append('')

        successors = sorted(list(state.successors), key=lambda f: f.identifier)
        if len(state.successors) == 1:
            code.append('{} = {};'.format(automaton.state_variable.name, successors[0].identifier))
        elif len(state.successors) == 2:
            code.extend([
                'if (ldv_undef_int())',
                '\t{} = {};'.format(automaton.state_variable.name, successors[0].identifier),
                'else',
                '\t{} = {};'.format(automaton.state_variable.name, successors[1].identifier),
            ])
        elif len(state.successors) > 2:
            code.append('switch (ldv_undef_int()) {')
            for index in range(len(successors)):
                code.append('\tcase {}: '.format(index) + '{')
                code.append('\t\t{} = {};'.format(automaton.state_variable.name, successors[index].identifier))
                code.append('\t\tbreak;'.format(automaton.state_variable.name, successors[index].identifier))
                code.append('\t}')
            code.append('\tdefault: ldv_stop();')
            code.append('}')
        else:
            code.append('/* Reset automaton state */')
            code.append('{} = {};'.format(automaton.state_variable.name, '0'))
            if self._nested_automata:
                code.append('goto out_{};'.format(automaton.identifier))

        return v_code, code

    def _state_cfunction(self, analysis, automaton):
        self.logger.info('Generate state-based control function for automaton {} based on process {} of category {}'.
                         format(automaton.identifier, automaton.process.name, automaton.process.category))
        v_code = []
        f_code = []
        tab = 0

        # Generate function definition
        cf = self.__new_control_function(analysis, automaton, v_code, f_code, None)

        f_code.append('/* Initialize state */')
        f_code.append('if (!{}) '.format(automaton.state_variable.name) + '{')
        initial_states = sorted(list(automaton.fsa.initial_states), key=lambda s: s.identifier)
        if len(initial_states) == 1:
            f_code.append('\t{} = {};'.format(automaton.state_variable.name, initial_states[0].identifier))
        elif len(initial_states) == 2:
            f_code.extend([
                '\tif (ldv_undef_int())',
                '\t\t{} = {};'.format(automaton.state_variable.name, initial_states[0].identifier),
                '\telse',
                '\t\t{} = {};'.format(automaton.state_variable.name, initial_states[1].identifier),
            ])
        elif len(initial_states) > 2:
            f_code.append('switch (ldv_undef_int()) {')
            for index in range(len(initial_states)):
                f_code.append('\t\tcase {}: '.format(index) + '{')
                f_code.append('\t\t\t{} = {};'.format(automaton.state_variable.name, initial_states[index].identifier))
                f_code.append('\t\t\tbreak;'.format(automaton.state_variable.name, initial_states[index].identifier))
                f_code.append('\t\t}')
                f_code.append('\t\tdefault: ldv_stop();')
                f_code.append('\t}')
        f_code.append('}')

        # Add loop for nested case
        if self._nested_automata:
            f_code.append('while (1) {')
            tab += 1

        if len(list(automaton.state_blocks.keys())) == 0:
            f_code.append('/* Empty control function */')
        else:
            if len(list(automaton.state_blocks)) == 1:
                new_v_code, new_f_code = self._state_sequence_code(analysis, automaton,
                                                                   list(automaton.state_blocks.values())[0])
                v_code.extend(new_v_code)
                f_code.extend(['\t' * tab + stm for stm in new_f_code])
            elif len(list(automaton.state_blocks)) == 2:
                first = True
                tab += 1
                for key in sorted(list(automaton.state_blocks.keys())):
                    if first:
                        f_code.append('\t' * (tab - 1) + 'if (ldv_undef_int()) {')
                        first = False
                    else:
                        f_code.append('\t' * (tab - 1) + 'else {')

                    new_v_code, new_f_code = self._state_sequence_code(analysis, automaton,
                                                                       list(automaton.state_blocks.values())[0])
                    v_code.extend(new_v_code)
                    f_code.append('\t' * tab + 'ldv_assume({} == {});'.format(automaton.state_variable.name, key))
                    f_code.extend(['\t' * tab + stm for stm in new_f_code])
                    f_code.append('\t' * tab + '}')
            else:
                f_code.append('\t' * tab + 'switch ({}) '.format(automaton.state_variable.name) + '{')
                tab += 1
                for case in sorted(list(automaton.state_blocks.keys())):
                    f_code.append('\t' * tab + 'case {}: '.format(case) + '{')
                    tab += 1
                    new_v_code, new_f_code = self._state_sequence_code(analysis, automaton,
                                                                       automaton.state_blocks[case])
                    v_code.extend(new_v_code)
                    f_code.extend(['\t' * tab + stm for stm in new_f_code])
                    f_code.append('\t' * tab + 'break;')
                    tab -= 1
                    f_code.append('\t' * tab + '}')
                f_code.append('\t' * tab + 'default: ldv_stop;')
                tab -= 1
                f_code.append('\t' * tab + '}')

        # Add loop for nested case
        if self._nested_automata:
            f_code.append('}')
            tab -= 1
            f_code.append('out_{}:'.format(automaton.identifier))
            f_code.append('return;')
            self._add_global_variable(automaton.thread_variable)
            v_code.append(automaton.state_variable.declare() + " = 0;")
        else:
            self._add_global_variable(automaton.state_variable)
        cf.body.concatenate(v_code + f_code)
        automaton.control_function = cf

        return cf

    def __generate_aspects(self):
        aspect_dir = "aspects"
        self.logger.info("Create directory for aspect files {}".format("aspects"))
        os.makedirs(aspect_dir, exist_ok=True)

        for grp in self.task['grps']:
            # Generate function declarations
            self.logger.info('Add aspects to C files of group "{0}"'.format(grp['id']))
            for cc_extra_full_desc_file in sorted([df for df in grp['cc extra full desc files'] if 'in file' in df],
                                                  key=lambda f: f['in file']):
                # Aspect text
                lines = list()

                if len(self.additional_aspects) > 0:
                    lines.append("\n")
                    lines.append("/* EMG additional non-generated aspects */\n")
                    lines.extend(self.additional_aspects)
                    lines.append("\n")

                # After file
                lines.append('after: file ("$this")\n')
                lines.append('{\n')

                lines.append("/* EMG type declarations */\n")
                for file in sorted(self.files.keys()):
                    if "types" in self.files[file]:
                        for tp in self.files[file]["types"]:
                            lines.append(tp.to_string('') + " {\n")
                            for field in sorted(list(tp.fields.keys())):
                                lines.append("\t{};\n".format(tp.fields[field].to_string(field)))
                            lines.append("};\n")
                            lines.append("\n")

                lines.append("/* EMG Function declarations */\n")
                for file in sorted(self.files.keys()):
                    if "functions" in self.files[file]:
                        for function in [self.files[file]["functions"][name] for name
                                         in sorted(self.files[file]["functions"].keys())]:
                            if function.export and cc_extra_full_desc_file["in file"] != file:
                                lines.extend(function.get_declaration(extern=True))
                            else:
                                lines.extend(function.get_declaration(extern=False))

                lines.append("\n")
                lines.append("/* EMG variable declarations */\n")
                for file in sorted(self.files):
                    if "variables" in self.files[file]:
                        for variable in [self.files[file]["variables"][name] for name in
                                         sorted(self.files[file]["variables"].keys())]:
                            if variable.export and cc_extra_full_desc_file["in file"] != file:
                                lines.extend([variable.declare(extern=True) + ";\n"])
                            else:
                                lines.extend([variable.declare(extern=False) + ";\n"])

                lines.append("\n")
                lines.append("/* EMG variable initialization */\n")
                for file in sorted(self.files):
                    if "variables" in self.files[file]:
                        for variable in [self.files[file]["variables"][name] for name in
                                         sorted(self.files[file]["variables"].keys())]:
                            if cc_extra_full_desc_file["in file"] == file and variable.value:
                                lines.extend([variable.declare_with_init() + ";\n"])

                lines.append("\n")
                lines.append("/* EMG function definitions */\n")
                for file in sorted(self.files):
                    if "functions" in self.files[file]:
                        for function in [self.files[file]["functions"][name] for name
                                         in sorted(self.files[file]["functions"].keys())]:
                            if cc_extra_full_desc_file["in file"] == file:
                                lines.extend(function.get_definition())
                                lines.append("\n")

                lines.append("}\n")
                lines.append("/* EMG kernel function models */\n")
                for aspect in self.model_aspects:
                    lines.extend(aspect.get_aspect())
                    lines.append("\n")

                name = "aspects/ldv_{}.aspect".format(os.path.splitext(
                    os.path.basename(cc_extra_full_desc_file["in file"]))[0])
                with open(name, "w", encoding="ascii") as fh:
                    fh.writelines(lines)

                path = os.path.relpath(os.path.abspath(name), os.path.realpath(self.conf['source tree root']))
                self.logger.info("Add aspect file {}".format(path))
                self.aspects[cc_extra_full_desc_file["in file"]] = path

    def __add_aspects(self):
        for grp in self.task['grps']:
            self.logger.info('Add aspects to C files of group "{0}"'.format(grp['id']))
            for cc_extra_full_desc_file in sorted([f for f in grp['cc extra full desc files'] if 'in file' in f],
                                                  key=lambda f: f['in file']):
                if cc_extra_full_desc_file["in file"] in self.aspects:
                    if 'plugin aspects' not in cc_extra_full_desc_file:
                        cc_extra_full_desc_file['plugin aspects'] = []
                    cc_extra_full_desc_file['plugin aspects'].append(
                        {
                            "plugin": "EMG",
                            "aspects": [self.aspects[cc_extra_full_desc_file["in file"]]]
                        }
                    )

    def __add_entry_points(self):
        self.task["entry points"] = [self.entry_point_name]

__author__ = 'Ilja Zakharov <ilja.zakharov@ispras.ru>'


