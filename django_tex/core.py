import os
from subprocess import PIPE, run
from logging import getLogger
from shlex import quote
import tempfile
from typing import Callable, Any, Dict
from django.template.loader import get_template
from django_tex.exceptions import TexError, PrintError
from django.conf import settings

logger = getLogger("django_tex")


DEFAULT_INTERPRETER = 'lualatex'
# Allow to run tex command multiple times
# (e.g., to allow the generation of a toc and to fix progress bars and to display correct total page counts)
DEFAULT_RUN_COUNT = 1

# Every printing command uses a different parameter to define the destination printer
# we define them here
PRINTING_DEVICE_OPTIONS = {
    'lp': "-d {}",
    'lpr': "-P {}",
}


class TexBuildCore:
    def __init__(self, source: str, base_filename: str = 'texput'):
        self.source = source
        self.base_filename = base_filename
        self._extra_printing_option: str = ""

    def _process_tex(self, call_back: Callable):
        """
        Process the tex file. Once finished the resulting dir and file will be given to the call back function
        that performs the last operations (currently either reading the file or printing it)
        :param call_back: callable that takes the parameters (tempdir, pdf_filename)
        :return: return value of call back function
        """
        if not callable(call_back):
            raise ValueError("parameter call_back needs to be callable!")
        with tempfile.TemporaryDirectory() as tempdir:
            filename = os.path.join(tempdir, f'{self.base_filename}.tex')
            with open(filename, 'x', encoding='utf-8') as f:
                f.write(self.source)
            latex_interpreter = getattr(settings, 'LATEX_INTERPRETER', DEFAULT_INTERPRETER)
            latex_run_count = getattr(settings, 'LATEX_RUN_COUNT', DEFAULT_RUN_COUNT)
            latex_interpreter_options = getattr(settings, 'LATEX_INTERPRETER_OPTIONS', '')
            latex_call = f' && {latex_interpreter} -interaction=batchmode {latex_interpreter_options} {os.path.basename(filename)}'
            latex_command = f'cd "{tempdir}" {latex_run_count * latex_call}'
            process = run(latex_command, shell=True, stdout=PIPE, stderr=PIPE)
            try:
                if process.returncode == 1:
                    with open(os.path.join(tempdir, 'texput.log'), encoding='utf8') as f:
                        log = f.read()
                    raise TexError(log=log, source=self.source)
                pdf_temp_file = os.path.join(tempdir, f'{self.base_filename}.pdf')
                if not os.path.isfile(pdf_temp_file):
                    raise FileNotFoundError(f"File {pdf_temp_file} not found or is not a file!")
                return call_back(tempdir, f'{self.base_filename}.pdf')
            except FileNotFoundError:
                if process.stderr:
                    raise Exception(process.stderr.decode('utf-8'))
                raise

    def _get_pdf_worker(self, tempdir: str, pdf_filename: str):
        with open(os.path.join(tempdir, pdf_filename), 'rb') as pdf_file:
            return pdf_file.read()

    def get_pdf(self):
        return self._process_tex(self._get_pdf_worker)

    def _print_pdf_worker_unix(self, tempdir: str, pdf_filename: str):
        pdf_filename = os.path.join(tempdir, pdf_filename)
        printer = getattr(settings, 'LATEX_PRINTER', None)
        print_command = getattr(settings, 'LATEX_UNIX_PRINT_COMMAND', 'lpr')
        print_options = getattr(settings, 'LATEX_UNIX_PRINT_OPTIONS', '')
        if printer is None:
            logger.debug("No default printer set, using default.")
        elif print_command not in PRINTING_DEVICE_OPTIONS:
            logger.warning(f"The unknown custom printing command '{print_command}' was defined and as well as a "
                           f"non default printer. Please edit 'PRINTING_DEVICE_OPTIONS' in the module according to your "
                           f"needs.")
        else:
            device_selection = PRINTING_DEVICE_OPTIONS[print_command].format(quote(printer))
            print_command = f"{print_command} {device_selection}"
        full_command = f"{print_command} {print_options} {self._extra_printing_option} {quote(pdf_filename)}"
        process = run(full_command, shell=True, stdout=PIPE, stderr=PIPE)
        if process.returncode != 0:
            raise PrintError(f"Printing with '{full_command}' resulted in an error. '{process.stderr.decode('utf-8')}'")
        if len(process.stderr) > 0:
            logger.warning(f"Printing with '{full_command}' did not result in an error but still wrote something, "
                           f"to stderr. ''{process.stderr.decode('utf-8')}")

    def print_pdf_unix(self, extra_printing_option: str = ""):
        self._extra_printing_option = extra_printing_option
        return self._process_tex(self._print_pdf_worker_unix)


def run_tex(source):
    build_core = TexBuildCore(source)
    return build_core.get_pdf()


def compile_template_to_pdf(template_name, context):
    source = render_template_with_context(template_name, context)
    return run_tex(source)


def compile_template_and_sent_to_printer(template_name: str, context: Dict[str, Any], extra_options=""):
    """
    Compile the template_name using context dict and sent the generated pdf to the printer
    :param template_name:  the latex template to fill and generate the pdf from
    :param context: data to fill the template
    :param extra_options: extra options for the printing command
    :exception PrintError - printing command failed
    :exception TexError - could no compile tex file
    :return: None
    """
    source = render_template_with_context(template_name, context)
    build_core = TexBuildCore(source)
    build_core.print_pdf_unix(extra_options)


def render_template_with_context(template_name: str, context: Dict[str, Any]):
    template = get_template(template_name, using='tex')
    return template.render(context)
