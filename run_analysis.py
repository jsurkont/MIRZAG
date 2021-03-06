"""
This script generates the jobs of the pipeline that will do the analysis on the split files.
"""
import glob
import os
import sys
from configobj import ConfigObj
from jinja2 import Template
from Jobber import JobClient
from argparse import ArgumentParser, RawTextHelpFormatter

parser = ArgumentParser(description=__doc__, formatter_class=RawTextHelpFormatter)
parser.add_argument("-v",
                    "--verbose",
                    dest="verbose",
                    action="store_true",
                    default=False,
                    help="Be loud!")
parser.add_argument("--config",
                    dest="config",
                    required=True,
                    help="Config file")
parser.add_argument("--group-id",
                    dest="group_id",
                    required=True,
                    help="Group Id")
parser.add_argument("--input-dir",
                    dest="input_dir",
                    required=True,
                    help="Input and output directory")
parser.add_argument("--working-dir",
                    dest="working_dir",
                    required=True,
                    help="Working directory of the pipeline. Required because this file is launched from ~")
parser.add_argument("--protocol",
                    dest="protocol",
                    default="seed",
                    choices=("seed", "scan"),
                    help="Protocol of MIRZA-G, defaults to seed")



def main(options):
    settings = ConfigObj(options.config).dict()

    thisDir = options.working_dir
    pip_dir = os.path.dirname(os.path.abspath(__file__))

    jobber = JobClient.Jobber()

    #We can call "extendGroup" if we want to create jobs into an already existing group. Don't forget to call "endGroup" and "launch"
    #after you're done
    jobber.extendGroup(options.group_id)

    #We assume that each file to be analyzed ends with .seqs. Its important to always distinguish input files from any intermediary
    #files in case we need to restart the jobs. We should make the jobs unique to prevent duplication of jobs in case this script
    #is run multiple times

    # template = settings['general']['template']
    template = os.path.join(pip_dir, "scripts/template.sh")
    with open(template) as tmpl:
        template = Template(tmpl.read())

    files_to_run = {}
    if options.protocol == "seed":
        seed_count_group = jobber.startGroup({'name': 'SeedCount'})
        for f in glob.glob(options.input_dir + "/*.fa"):
            input_name = os.path.splitext(f)[0]

            #
            # Calculate seed matches
            #
            seed_count_settings = settings['tasks']['CalculateSeedMatches']
            seed_count_script = 'scripts/rg_count_miRNA_seeds_and_filter_duplicates.py'
            seed_count_command = """python {script} \\
                                        --motifs {input} \\
                                        --seqs {seqs} \\
                                        --output {output} \\
                                        --how {how} \\
                                        --context {context} \\
                                        --split-by "{split_by}" \\
                                        --index-after-split {index_after_split} \\
                                        -v
                          """
            #
            # If there is template use it for command
            #
            if settings['general'].get('executer', 'drmaa') == 'drmaa':
                #
                # Copy files by default to the tmp directory
                #
                copy_dir = "$TMPDIR"
                copy_files = {f: 'mirnas.fa',
                              settings['general']['seqs']: 'seqs.fa'}
                moveback = {'output': input_name + ".seedcount"}

                seed_command_rendered = template.render(modules=seed_count_settings.get('modules', None),
                                                   command=seed_count_command,
                                                   copy=copy_files,
                                                   moveback=moveback,
                                                   copydir=copy_dir)
                seed_count_command = str(seed_command_rendered).format(**{'script': os.path.join(pip_dir, seed_count_script),
                                               'input': 'mirnas.fa',
                                               'seqs': 'seqs.fa',
                                               'output': 'output',
                                               'how': seed_count_settings.get('how', 'TargetScan'),
                                                'split_by': settings['general'].get('split_by', "NONE"),
                                               'index_after_split': settings['general'].get('index_after_split', 0),
                                               'context': seed_count_settings.get('context', 50)})
            else:
                seed_count_command = str(seed_count_command).format(**{'script': os.path.join(pip_dir, seed_count_script),
                                               'input': f,
                                               'seqs': settings['general']['seqs'],
                                               'output': input_name + ".seedcount",
                                               'how': seed_count_settings.get('how', 'TargetScan'),
                                                'split_by': settings['general'].get('split_by', "NONE"),
                                               'index_after_split': settings['general'].get('index_after_split', 0),
                                               'context': seed_count_settings.get('context', 50)})
            seed_count_id = jobber.job(seed_count_command,
                                       {'name': 'SeedCount',
                                        'uniqueId': True,
                                        'options': [('q', seed_count_settings.get('queue', 'short.q')),
                                                    ('l', "membycore=%s" % seed_count_settings.get('mem_req', '2G'))]
                                        })
            files_to_run[input_name] = seed_count_id

        jobber.endGroup()

    if options.protocol == 'scan':
        for f in glob.glob(options.input_dir + "/*.seedcount"):
            input_name = os.path.splitext(f)[0]
            files_to_run[input_name] = None

    features_group_id = jobber.startGroup({'name': "Features_Group"})

    #
    # MIRZA
    #
    mirza_group = jobber.startGroup({'name': 'MIRZA'})
    for input_name, seed_count_id in files_to_run.iteritems():
        #
        # Create group for each file in order to calculate features
        #
        mirza_settings = settings['tasks']['CalculateMIRZA']
        mirza_script = 'scripts/rg_calculate_MIRZA.py'
        calculate_mirza_command = """python {script} \\
                        --out {output} \\
                        --seq {seqs} \\
                        --coords {input} \\
                        --motifs {motifs} \\
                        --contextLen {context} \\
                        --reforg {reforg} \\
                        --tree {tree} \\
                        --mln-dir {mlndir} \\
                        --threshold {threshold} \\
                        --onlyMIRZA {onlymirza} \\
                        --mirzabin {mirzabin} \\
                        -v
                  """

        if settings['general'].get('executer', 'drmaa') == 'drmaa':
            #
            # Copy files by default to the tmp directory
            #
            copy_dir = "$TMPDIR"
            copy_files = {input_name + ".seedcount": 'input.seedcount',
                          settings['general']['seqs']: 'seqs.fa',
                          settings['general']['motifs']: 'motifs.fa'}
            moveback = {'output': input_name + ".mirza"}

            calculate_mirza_command_rendered = template.render(modules=mirza_settings.get('modules', None),
                                                               command=calculate_mirza_command,
                                                               copy=copy_files,
                                                               moveback=moveback,
                                                               copydir=copy_dir)
            calculate_mirza_command = str(calculate_mirza_command_rendered).format(**{'script': os.path.join(pip_dir, mirza_script),
                                'output': 'output',
                                'input': "input.seedcount",
                                'seqs': 'seqs.fa',
                                'motifs': 'motifs.fa',
                                'context': mirza_settings.get('context_length', 50),
                                'reforg': mirza_settings.get('reference_organism', 'any/path'),
                                'tree': mirza_settings.get('phylogenetic_tree', 'any/path'),
                                'mlndir': mirza_settings.get('alignment_directory', 'any/path'),
                                'threshold': mirza_settings.get('threshold', 50),
                                'onlymirza': settings['general'].get('run_only_MIRZA', "yes"),
                                'mirzabin': settings['general']['mirza_binary'],
                                })
        else:
            calculate_mirza_command = str(calculate_mirza_command).format(**{'script': os.path.join(pip_dir, mirza_script),
                                'output': input_name + ".mirza",
                                'input': input_name + ".seedcount",
                                'seqs': settings['general']['seqs'],
                                'motifs': settings['general']['motifs'],
                                'context': mirza_settings.get('context_length', 50),
                                'reforg': mirza_settings.get('reference_organism', 'any/path'),
                                'tree': mirza_settings.get('phylogenetic_tree', 'any/path'),
                                'mlndir': mirza_settings.get('alignment_directory', 'any/path'),
                                'threshold': mirza_settings.get('threshold', 50),
                                'onlymirza': settings['general'].get('run_only_MIRZA', "yes"),
                                'mirzabin': settings['general']['mirza_binary'],
                                })

        calculate_mirza_id = jobber.job(calculate_mirza_command, {
                                          'name': 'CalculateMIRZA',
                                          'dependencies': [seed_count_id] if seed_count_id is not None else [],
                                           'options': [('q', mirza_settings.get('queue', 'short.q')),
                                                       ('l', "membycore=%s" % mirza_settings.get('mem_req', '2G'))],
                                          'uniqueId': True})

    jobber.endGroup()
    #
    # Contrafold
    #
    contrafold_group = jobber.startGroup({'name': 'CONTRAfold'})
    for input_name, seed_count_id in files_to_run.iteritems():
        contrafold_settings = settings['tasks']['CalculateCONTRAfold']
        contrafold_script = 'scripts/rg_calculate_contrafold.py'
        contrafold_command = """python {script} \\
                                    --out {output} \\
                                    --seq {seqs} \\
                                    --coords {input} \\
                                    --contextLen_L {contextlen_l} \\
                                    --contextLen_U {contextlen_u} \\
                                    --context {context} \\
                                    --contrabin {contrabin} \\
                                    -v
                              """
        if settings['general'].get('executer', 'drmaa') == 'drmaa':
            #
            # Copy files by default to the tmp directory
            #
            copy_dir = "$TMPDIR"
            copy_files = {input_name + ".seedcount": 'input.seedcount',
                          settings['general']['seqs']: 'seqs.fa'}
            moveback = {'output': input_name + ".contrafold"}

            contrafold_command_rendered = template.render(modules=contrafold_settings.get('modules', None),
                                                          command=contrafold_command,
                                                          copy=copy_files,
                                                          moveback=moveback,
                                                          copydir=copy_dir)
            contrafold_command = str(contrafold_command_rendered).format(**{'script': os.path.join(pip_dir, contrafold_script),
                                    'output': "output",
                                    'input': "input.seedcount",
                                    'seqs': 'seqs.fa',
                                    'context': contrafold_settings.get('context', 50),
                                    'contextlen_l': contrafold_settings.get('contextLen_L', 14),
                                    'contextlen_u': contrafold_settings.get('contextLen_U', 0),
                                    'contrabin': settings['general']['contrafold_binary']
                                     })
        else:
            contrafold_command = str(contrafold_command).format(**{'script': os.path.join(pip_dir, contrafold_script),
                                    'output': input_name + ".contrafold",
                                    'input': input_name + ".seedcount",
                                    'seqs': settings['general']['seqs'],
                                    'context': contrafold_settings.get('context', 50),
                                    'contextlen_l': contrafold_settings.get('contextLen_L', 14),
                                    'contextlen_u': contrafold_settings.get('contextLen_U', 0),
                                    'contrabin': settings['general']['contrafold_binary']
                                     })

        calculate_contrafold_id = jobber.job(contrafold_command, {
                                          'name': 'CalculateCONTRAfold',
                                          'dependencies': [seed_count_id] if seed_count_id is not None else [],
                                           'options': [('q', contrafold_settings.get('queue', 'short.q')),
                                                       ('l', "membycore=%s" % contrafold_settings.get('mem_req', '2G'))],
                                          'uniqueId': True})
    jobber.endGroup()
    #
    # Flanks
    #
    flanks_group = jobber.startGroup({'name': 'Flanks'})
    for input_name, seed_count_id in files_to_run.iteritems():
        calculate_flanks_settings = settings['tasks']['CalculateFlanks']
        flanks_script = 'scripts/rg_calculate_flanks_composition.py'
        flanks_command = """python {script} \\
                                --out {output} \\
                                --seq {seqs} \\
                                --coords {input} \\
                                --contextLen {context} \\
                                -v
                          """
        if settings['general'].get('executer', 'drmaa') == 'drmaa':
            #
            # Copy files by default to the tmp directory
            #
            copy_dir = "$TMPDIR"
            copy_files = {input_name + ".seedcount": 'input.seedcount',
                          settings['general']['seqs']: 'seqs.fa'}
            moveback = {'output': input_name + ".flanks"}

            flanks_command_rendered = template.render(modules=calculate_flanks_settings.get('modules', None),
                                                          command=flanks_command,
                                                          copy=copy_files,
                                                          moveback=moveback,
                                                          copydir=copy_dir)

            flanks_command = str(flanks_command_rendered).format(**{'script': os.path.join(pip_dir, flanks_script),
                                       'output': "output",
                                       'input': "input.seedcount",
                                       'seqs': 'seqs.fa',
                                       'context': calculate_flanks_settings.get('context_length', 50),
                                     })
        else:
            flanks_command = str(flanks_command).format(**{'script': os.path.join(pip_dir, flanks_script),
                                       'output': input_name + ".flanks",
                                       'input': input_name + ".seedcount",
                                       'seqs': settings['general']['seqs'],
                                       'context': calculate_flanks_settings.get('context_length', 50),
                                     })
        calculate_flanks_id = jobber.job(flanks_command, {
                                          'name': 'CalculateFlanks',
                                          'dependencies': [seed_count_id] if seed_count_id is not None else [],
                                           'options': [('q', calculate_flanks_settings.get('queue', 'short.q')),
                                                       ('l', "membycore=%s" % calculate_flanks_settings.get('mem_req', '2G'))],
                                          'uniqueId': True})

    jobber.endGroup()
    #
    # Distance
    #
    distance_group = jobber.startGroup({'name': 'Distance'})
    for input_name, seed_count_id in files_to_run.iteritems():
        calculate_distance_settings = settings['tasks']['CalculateDistance']
        distance_script = 'scripts/rg_calculate_distance.py'
        distance_command = """python {script} \\
                                --out {output} \\
                                --seq {seqs} \\
                                --coords {input} \\
                                -v
                          """
        if settings['general'].get('executer', 'drmaa') == 'drmaa':
            #
            # Copy files by default to the tmp directory
            #
            copy_dir = "$TMPDIR"
            copy_files = {input_name + ".seedcount": 'input.seedcount',
                          settings['general']['seqs']: 'seqs.fa'}
            moveback = {'output': input_name + ".distance"}

            distance_command_rendered = template.render(modules=calculate_distance_settings.get('modules', None),
                                                          command=distance_command,
                                                          copy=copy_files,
                                                          moveback=moveback,
                                                          copydir=copy_dir)

            distance_command = str(distance_command_rendered).format(**{'script': os.path.join(pip_dir, distance_script),
                                         'output': "output",
                                         'input': "input.seedcount",
                                         'seqs': "seqs.fa",
                                        })
        else:
            distance_command = str(distance_command).format(**{'script': os.path.join(pip_dir, distance_script),
                                         'output': input_name + ".distance",
                                         'input': input_name + ".seedcount",
                                         'seqs': settings['general']['seqs'],
                                        })
        calculate_distance_id = jobber.job(distance_command, {
                                          'name': 'CalculateDistance',
                                          'dependencies': [seed_count_id] if seed_count_id is not None else [],
                                           'options': [('q', calculate_distance_settings.get('queue', 'short.q')),
                                                       ('l', "membycore=%s" % calculate_distance_settings.get('mem_req', '2G'))],
                                          'uniqueId': True})
    jobber.endGroup()
    jobber.endGroup()
    #
    # Merge and add probabilities
    #
    add_probability_group = jobber.startGroup({'name': 'MergeAndAddProbability'})
    for input_name, seed_count_id in files_to_run.iteritems():

        merge_script = 'scripts/rg_merge_results_add_probability_and_calculate_per_gene_score.py'
        merge_settings = settings['tasks']['MergeAndCollect']
        merge_inputs_local = ",".join([input_name + ".contrafold",
                                 input_name + ".mirza",
                                 input_name + ".flanks",
                                 input_name + ".distance"])
        merge_command = """python {script} \\
                            --output {output} \\
                            --inputs {inputs} \\
                            --coords {coords} \\
                            --model-bls {model_bls} \\
                            --model-nobls {model_nobls} \\
                            --only-mirza {onlymirza} \\
                            --threshold {threshold} \\
                            --split-by "{split_by}" \\
                            --colum {column} \\
                            -v
                  """
        if settings['general'].get('executer', 'drmaa') == 'drmaa':
            #
            # Copy files by default to the tmp directory
            #
            copy_dir = "$TMPDIR"
            copy_files  = {input_name + ".contrafold": "contrafold",
                           input_name + ".mirza": "mirza",
                           input_name + ".flanks": "flanks",
                           input_name + ".distance": "distance",
                           input_name + ".seedcount": "input.seedcount"}
            moveback = {'output': input_name + ".score"}

            merge_command_rendered = template.render(modules=merge_settings.get('modules', None),
                                                     command=merge_command,
                                                     copy=copy_files,
                                                     moveback=moveback,
                                                     copydir=copy_dir)

            merge_command = str(merge_command_rendered).format(**{'script': os.path.join(pip_dir, merge_script),
                                      'output': "output",
                                      'inputs': "contrafold,mirza,flanks,distance",
                                      'coords': "input.seedcount",
                                      'model_bls':   settings['general']['model_with_bls'],
                                      'model_nobls': settings['general']['model_without_bls'],
                                      'onlymirza': settings['general'].get('run_only_MIRZA', 'yes'),
                                      'threshold': merge_settings.get('threshold', 0.12),
                                      'split_by':  settings['general'].get('split_by', "NOTHING"),
                                      'column':    settings['general'].get('index_after_split', 0),
                                     })
        else:
            merge_command = str(merge_command).format(**{'script': os.path.join(pip_dir, merge_script),
                                      'output': input_name + ".score",
                                      'inputs': merge_inputs_local,
                                      'coords': input_name + ".seedcount",
                                      'model_bls':   settings['general']['model_with_bls'],
                                      'model_nobls': settings['general']['model_without_bls'],
                                      'onlymirza': settings['general'].get('run_only_MIRZA', 'yes'),
                                      'threshold': merge_settings.get('threshold', 0.12),
                                      'split_by':  settings['general'].get('split_by', "NOTHING"),
                                      'column':    settings['general'].get('index_after_split', 0),
                                     })
        merge_id = jobber.job(merge_command, {
                                          'name': 'MergeAndCollect',
                                          'dependencies': [features_group_id],
                                           'options': [('q', merge_settings.get('queue', 'short.q')),
                                                       ('l', "membycore=%s" % merge_settings.get('mem_req', '2G'))],
                                          'uniqueId': True})

    jobber.endGroup()
    jobber.endGroup()
    jobber.launch(options.group_id)

if __name__ == '__main__':
    try:
        options = parser.parse_args()
    except Exception, e:
        parser.print_help()
        sys.exit()
    main(options)
