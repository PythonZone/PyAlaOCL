# coding=utf-8
"""
Evaluate a set of USE OCL states against a given USE OCL model and store the
evaluation result. This evaluation result is basically a map yielding for
each state file the corresponding ModelEvaluation (either ModelValidation or
ModelFailure indeed).
"""

import logging

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger('test.' + __name__)

import tempfile
import os
import re
from collections import OrderedDict

import pyalaocl.useocl.soil

import pyalaocl.useocl.analyzer
from pyalaocl.useocl.analyzer import UseOCLModel

import pyalaocl.useocl.useengine
from pyalaocl.useocl.useengine import USEEngine


import pyalaocl.useocl.evaluation
from evaluation \
    import ModelValidation, ModelViolation, InvariantViolation, \
    InvariantValidation, \
    CardinalityViolation


class UseEvaluationResults(object):
    """
    Results of the evaluation of a list of state file against a given model.

    The output of USE OCL check command look like following and this class
    basically represented this information in a structure way for the
    different state files::

        use >  check -d
        checking structure...
        Multiplicity constraint violation in association `ASSOC':
          Object `OBJECT' of class `CLASS' is connected to NUM objects of class `CLASS'
          at association end `END' but the multiplicity is specified as `NUM'.
        checking invariants...
        checking invariant (NUM) `CLASS::INVARIANT': OK.
        checking invariant (NUM) `CLASS::INVARIANT': FAILED.
          -> false : Boolean
        Instances of CLASS violating the invariant:
          -> Set{@bedroom201,@bedroom202, ...} : Set(Bedroom)
    """
    def __init__(self, useOCLModel, soilFiles):
        """
        Evaluate a list of stateFiles against a given model and store the
        use_evaluation_result results.
        :param useOCLModel: a valid UseOCLModel build with the analyzer.
        :type useOCLModel: analyzer.UseOCLModel
        :param stateFiles: list of paths to state files (.soil)
        :type stateFiles: [str]

        """
        log.info('UseEvaluationResults.__init__(%s)', str(len(soilFiles)))
        self.useOCLModel = useOCLModel

        self.modelFile = self.useOCLModel.fileName
        """ [str] """

        self.emptyStateFiles = []   # computed by __filterNonEmptyStateFiles
        """ [str] """

        self.stateFiles = []        # computed by __filterNonEmptyStateFiles
        """ [str] """

        self.__filterNonEmptyStateFiles(soilFiles)

        self.modelEvaluationMap = OrderedDict()
        """ dict(str,ModelEvaluation) """

        self.commandExitCode = \
            USEEngine.evaluateSoilFilesWithUSEModel(
                self.modelFile,
                self.stateFiles)
        self.wasExecutionValid = (self.commandExitCode == 0)

        if self.wasExecutionValid:
            self.output_text = USEEngine.outAndErr
            self.__parseValidationOutput(self.output_text)


    def __repr__(self):
        return ('UseEvaluationResults(commandExitCode=%s,modelEvaluations=%s)'
                % ( self.commandExitCode, self.modelEvaluationMap) )

    def __filterNonEmptyStateFiles(self, files):
        """
        Empty soil file should be removed because of a BUG in
        :return:
        :rtype:
        """
        for file in files:
            if pyalaocl.useocl.soil.isNonEmptySoilFile(file):
                self.stateFiles.append(file)
            else:
                self.emptyStateFiles.append(file)


    def __parseValidationOutput(self, text):

        def __splitOutputAsSectionsGroups(output_text):
            """
            Parse the whole output file and split in in a list of section
            groups.
            :param output_text: The whole output of the use command.
            :type output_text: str
            :return: A list of groups, each group with 3 sections.
            :rtype: [{'stateSection':str,
                      'checkingStructureSection':str,
                      'checkingInvariantsSection': str}]
            """
            # Note the use of .*? for non-greedy matches.
            r = '^[^\n]+\.soil> open [^\n]*\.soil$' \
                '(?P<stateSection>.*?)' \
                '^[^\n]+\.soil> check -d$\n' \
                '^checking structure\.\.\.$' \
                '(?P<checkingStructureSection>.*?)' \
                '^checking invariants\.\.\.$' \
                '(?P<checkingInvariantsSection>.*?)' \
                '^checked \d+ invariants'

            _ = []
            for m in re.finditer(r, output_text, flags=re.DOTALL|re.MULTILINE):
                _.append( {
                    'stateSection' : m.group('stateSection'),
                    'checkingStructureSection' :
                        m.group('checkingStructureSection'),
                    'checkingInvariantsSection':
                        m.group('checkingInvariantsSection'),
                } )
            return _

        def __parseCheckingStructureSection(checkingStructureText):
            r1 = r'^Multiplicity constraint violation in association ' \
                 r'`(?P<association>\w+)\':$'
            r2 = r'^  Object `(?P<object>\w+)\' of class ' \
                 r'`(?P<sourceClass>\w+)\' is connected to ' \
                 r'(?P<numberOfObjects>\d+) objects of class ' \
                 r'`(?P<targetClass>\w+)\'$'
            r3 = r'^  at association end `(?P<role>\w+)\' but the ' \
                 r'multiplicity is specified as `(?P<cardinality>[^\']+)\'.$'
            r = r'\n'.join([r1, r2, r3])
            _ = []
            for m in re.finditer(r, checkingStructureText,
                                 flags=re.MULTILINE):
                _.append(m.groupdict())
            return _

        def __buildCardinalityViolation(info, model, modelViolation):
            role = model.findRole(info['association'] ,info['role'])
            violatingObject = None # FIXME
            cardinalityFound = int(info['numberOfObjects'])
            CardinalityViolation(modelViolation, role, violatingObject,
                                cardinalityFound)


        def __parseCheckingInvariantsSection(checkingInvariantsText):

            rOK = r'^checking invariant \([0-9]+\) `' \
                  r'(?P<class>\w+)::(?P<invariant>\w+)\': OK\.'


            rKO1 = r'checking invariant \([0-9]+\) `' \
                   r'(?P<class>\w+)::(?P<invariant>\w+)\': FAILED\.'
            rKO2 = r'^  -> (?P<result>.*?)'
            rKO3 = r'^Instances of (?P<class2>\w+) violating the invariant:'
            rKO4 = r'  -> Set{(?P<objectList>[\w@, ]+)} : Set\((?P<class3>\w+)'
            rKO  = r'\n'.join([rKO1,rKO2,rKO3,rKO4])

            oks = []
            for m in re.finditer(rOK, checkingInvariantsText,
                                    flags=re.MULTILINE):
                oks.append(m.groupdict())

            kos = []
            for m in re.finditer(rKO, checkingInvariantsText,
                                    flags=re.MULTILINE):
                objects = m.group('objectList').replace('@','').split(',')
                kos.append(dict(m.groupdict().items()+[('objects',objects)]))

            return (oks,kos)


        def __buildInvariantValidation(info, model, modelEvaluation):
            invariant = model.findInvariant(info['class'], info['invariant'])
            InvariantValidation(modelEvaluation, invariant)

        def __buildInvariantViolation(info, model, modelViolation):
            invariant = model.findInvariant(info['class'],info['invariant'])
            for objectName in info['objects']:
                pass #FIXME
            violatingObjects = [] #FIXME
            InvariantViolation(modelViolation, invariant, violatingObjects)


        sections_groups = __splitOutputAsSectionsGroups(text)
        for (state_index, state_sections) in enumerate(sections_groups):
            state_file = self.stateFiles[state_index]
            state_text = state_sections['stateSection']
            structure_text = state_sections['checkingStructureSection']
            invariant_text = state_sections['checkingInvariantsSection']

            # parse the evaluation part
            structure_violations = \
                __parseCheckingStructureSection(structure_text)
            (invariant_validations, invariant_violations) = \
                __parseCheckingInvariantsSection(invariant_text)
            # check if there is some errors
            model_validated = (
                len(structure_violations)==0
                and len(invariant_violations)==0)
            model = self.useOCLModel.model

            if model_validated:
                model_evaluation = ModelValidation(model, state_file)

            else:
                # there are some violations.
                model_evaluation = ModelViolation(model, state_file)

                for info in structure_violations:
                    __buildCardinalityViolation(info, model,
                                                model_evaluation)

                for info in invariant_violations:
                    __buildInvariantViolation(info, model,
                                              model_evaluation)
            for info in invariant_validations:
                __buildInvariantValidation(info, model, model_evaluation)
            self.modelEvaluationMap[state_file] = model_evaluation


