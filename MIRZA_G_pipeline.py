"""
The main script launching MIRZA-G analysis pipeline
"""

__date_ = "2015-05-29"
__author__ = "Rafal Gumienny"
__email__ = "r.gumienny@unibas.ch"
__version__ = "2.0.0"


import os
import sys
import shutil
import random
import traceback
from errno import EEXIST
from Jobber import JobClient

from configobj import ConfigObj
from argparse import ArgumentParser, RawTextHelpFormatter


parser = ArgumentParser(description=__doc__, formatter_class=RawTextHelpFormatter)
subparsers = parser.add_subparsers(help="Commands", dest='command')
run_parser = subparsers.add_parser("run", help="Run a pipeline")
run_parser.add_argument("-v",
                    "--verbose",
                    dest="verbose",
                    action="store_true",
                    default=False,
                    help="Be loud!")
run_parser.add_argument("--config",
                    dest="config",
                    required=True,
                    help="Config file")
run_parser.add_argument("--name-suffix",
                    dest="name_suffix",
                    default="test_run",
                    help="Suffix to add to pipeline name in order to easily differentiate between different run, defaults to test_run")
run_parser.add_argument("--protocol",
                    dest="protocol",
                    default="seed",
                    choices=("seed", "scan"),
                    help="Protocol of MIRZA-G, defaults to seed")
run_parser.add_argument("--calulate-bls",
                    dest="calulate_bls",
                    action="store_true",
                    default=False,
                    help="NOT AVAILABLE: Calculate Branch Length Score (conservation)")
run_parser.add_argument("--modules",
                    dest="modules",
                    nargs="*",
                    help="A list of modules to load (if HPC or environment requires)")

clean_parser = subparsers.add_parser("clean", help="Clean after previous run")
clean_parser.add_argument("-v",
                    "--verbose",
                    dest="verbose",
                    action="store_true",
                    default=False,
                    help="Be loud!")
clean_parser.add_argument("-y",
                        "--yes",
                        dest="yes",
                        action="store_true",
                        default=False,
                        help="Force deletion of files.")




# redefine a functions for writing to stdout and stderr to save some writting
syserr = sys.stderr.write
sysout = sys.stdout.write

def main(options):
    working_directory = os.getcwd()
    pipeline_directory = os.path.dirname(os.path.abspath(__file__))
    output_directory = os.path.join(working_directory, "output")


    if options.command == 'clean':
        try:
            if options.yes:
                is_sure = "yes"
            else:
                is_sure = raw_input("Do you really want to delete previous run (yes/no)?:  ")
            if is_sure.upper().startswith("Y"):
                try:
                    shutil.rmtree(output_directory)
                except OSError:
                    if options.verbose:
                        syserr(" -> no such file or directory: %s\n" % output_directory)
                files_to_delete = ["mirza_g_results_scan.tab",
                                   "mirza_g_results_seed.tab"]
                for f in files_to_delete:
                    if options.verbose:
                        syserr("Removing %s\n" % os.path.join(working_directory, f))
                    try:
                        os.remove(os.path.join(working_directory, f))
                    except OSError, e:
                        if options.verbose:
                            syserr(" -> no such file or directory: %s\n" % os.path.join(working_directory, f))
                if options.verbose:
                    syserr("All output files and directories were cleaned\n")
        except Exception as e:
            syserr(traceback.format_exc())
        finally:
            sys.exit()

    settings = ConfigObj(options.config).dict()
    mkdir_p(output_directory)
    if options.protocol == "scan":
        mkdir_p(os.path.join(output_directory, "MIRZAscan"))

    # checking only works locally because on HPC other software might be
    # installed and it can have different path. However mind that this might
    # also not work if environment uses modules
    if settings['general'].get('executer', 'drmaa') == 'local':
        #
        # Check if path to CONTRAfold is valid
        #
        if not is_executable(settings['general']['contrafold_binary']):
            raise Exception("Path to CONTRAfold is invalid (%s)! Please define it with --contrabin option." % settings['general']['contrafold_binary'])
        #
        # Check if path to MIRZA is valid
        #
        if not is_executable(settings['general']['mirza_binary']):
            raise Exception("Path to MIRZA is invalid (%s)! Please define it with --mirzabin option." % settings['general']['mirza_binary'])


    jobber = JobClient.Jobber()

    #Create a group for whole pipeline. The module "Python" will be inherited by all jobs that are in this group,
    # so we don't need to define it for each job that calls a python script
    if options.modules is not None:
        pipeline_id = jobber.startGroup({'name': "MIRZA-G_%s" % options.name_suffix,
                                         'options': [['module', module] for module in options.modules],
                                         'executer': settings['general'].get('executer', 'drmaa')})
    else:
        pipeline_id = jobber.startGroup({'name': "MIRZA-G_%s" % options.name_suffix,
                                         'executer': settings['general'].get('executer', 'drmaa')})

    if options.protocol == "seed":

        #First step is to split the file
        split_command = "python %s --input %s --output-dir %s" % (os.path.join(pipeline_directory, "scripts/rg_prepare_mirnas_for_mirza_and_split.py"),
                                                                  settings['general']['motifs'],
                                                                  output_directory)
        split_files_id = jobber.job(split_command, {'name': "SplitMiRNAs"})

    if options.protocol == "scan":
        #First step is to split the file
        make_chunks = "python %s --input %s --output-dir %s -v" % (os.path.join(pipeline_directory, "scripts/rg_generate_utr_chunks.py"),
                                                                     settings['general']['seqs'],
                                                                     os.path.join(output_directory, "MIRZAscan"))
        make_chunks_id = jobber.job(make_chunks, {'name': "GenerateChunks"})

        # generate expressions
        gen_expr_tup = (settings['general']['motifs'],
                        os.path.join(output_directory, "MIRZAscan/mirnas.expression"))
        gen_expr_command = """cat %s | ruby -ne 'puts "#{$_.rstrip()[1..-1]}\t1" if $_.start_with?(">")' > %s""" % gen_expr_tup
        gen_expressions_id = jobber.job(gen_expr_command, {'name': "GenerateExpressions"})

        # We create a group where the jobs to analyse the splitted files will be put into and MIRZA will be calculated
        mirza_scan_id = jobber.startGroup({'name': "MIRZAscan",
                                           'dependencies': [make_chunks_id, gen_expressions_id]})

        #We call the script that will generate the jobs that will analyse the split files. We pass the id of the group
        #and the folder where the script will find the splitted files.
        scan_tuple = (os.path.join(pipeline_directory, "run_mirza_scan.py"),
                          os.path.join(output_directory, "MIRZAscan"),
                          mirza_scan_id,
                          os.path.abspath(options.config),
                          working_directory)
        scan_command = "python %s --input-dir %s --group-id %s --config %s -v --working-dir %s" % scan_tuple
        jobber.job(scan_command, {'name': "createScanJobs"})


        jobber.endGroup()

        split_command = """awk '{print | "gzip > %s/"$2".seedcount"}' %s""" % (output_directory,
                                                                        os.path.join(output_directory, "MIRZAscan/scan_result.filtered"))

        split_files_id = jobber.job(split_command, {'name': "SplitCoords",
                                                    'dependencies': [mirza_scan_id]})


    #We create a group where the jobs to analyse the splitted files will be put into
    analyse_files_id = jobber.startGroup({'name': "Analysis",
                                        'dependencies': [split_files_id]})

    #We call the script that will generate the jobs that will analyse the split files. We pass the id of the group
    #and the folder where the script will find the splitted files.
    analysis_tuple = (os.path.join(pipeline_directory, "run_analysis.py"),
                      output_directory,
                      analyse_files_id,
                      os.path.abspath(options.config),
                      working_directory,
                      options.protocol)
    analysis_command = "python %s --input-dir %s --group-id %s --config %s -v --working-dir %s --protocol %s" % analysis_tuple
    jobber.job(analysis_command, {'name': "CreateAnalysisJobs",
                                  'options': [('q', 'long.q')],
                                  })


    jobber.endGroup()

    # We merge the files into our result file after analysis finishes
    final_merge_command = "zcat {output_dir}/*.score > {cwd}/mirza_g_results_{protocol}.tab".format(output_dir=output_directory,
                                                                                         cwd=working_directory,
                                                                                                    protocol=options.protocol)
    jobber.job(final_merge_command, {'name': "MergeResults",
                                     'dependencies': [analyse_files_id]})


    jobber.endGroup()

    # Before launching we print the command to stop the pipeline
    syserr("In order to stop the pipeline run a command:\n")
    syserr("jobber_server -command delete -jobId %i\n" % (pipeline_id))

    #You need to always launch, otherwise jobs wont get executed.
    jobber.launch(pipeline_id)


def mkdir_p(path_to_dir):
    """Make directory with subdirectories"""
    try:
        os.makedirs(path_to_dir)
    except OSError as e: # Python >2.5
        if e.errno == EEXIST and os.path.isdir(path_to_dir):
            message = "Output directory %s (and files) exist.\nYou need to clean before you proceed. Run:\n" + \
            "python MIRZA-G_pipeline clean \n"
            sys.stderr.write(message % path_to_dir)
            sys.exit()
        else:
            raise e


def is_executable(program):
    """
    Check if the path/binary provided is valid executable
    """
    def is_exe(fpath):
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

    fpath, fname = os.path.split(program)
    if fpath:
        if is_exe(program):
            return True
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            path = path.strip('"')
            exe_file = os.path.join(path, program)
            if is_exe(exe_file):
                return True

    return False


if __name__ == '__main__':
    try:
        options = parser.parse_args()
    except Exception, e:
        parser.print_help()
        sys.exit()
    main(options)
