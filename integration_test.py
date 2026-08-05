#!/usr/bin/env python3
# Copyright (C) 2026  Jakob
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import argparse
import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
# Both modules must be staged together: Kalico resolves [hotendchanger_tool]
# sections to the module of that name, so staging only hotendchanger.py
# leaves the tool sections unclaimed and config load fails.
PLUGIN_MODULES = ('hotendchanger.py', 'hotendchanger_tool.py')
PLUGIN_SRCS = tuple(REPO_DIR / name for name in PLUGIN_MODULES)
CASE_DIR = REPO_DIR / 'integration'

# The linuxprocess target builds with the host compiler, so no
# cross-toolchain is needed for the simulated MCU the cases run against.
MCU_TARGET = 'linuxprocess'
DICT_NAME = '%s.dict' % (MCU_TARGET,)

CASE_TIMEOUT = 600.0

# Kalico scripts/test_klippy.py:13: the harness gives klippy a log file of
# this name relative to its own working directory, unless it was asked for
# verbose output: then klippy has no log file and Python's fallback handler
# puts only warnings and worse on stderr, well above the level the markers
# below are logged at.
KLIPPY_LOG = '_test_.log'

# Where a firmware layout loads modules from, and how its C helper builds.
# Kalico installs under klippy/plugins/ imported as a klippy submodule (the
# directory may not exist yet and needs a package marker); stock Klipper
# loads from klippy/extras/ as a plain package with no marker.
LAYOUTS = {
    'kalico': {
        'install_dir': ('klippy', 'plugins'),
        'create_package_marker': True,
        'chelper_code': 'import klippy.chelper; klippy.chelper.get_ffi()',
        'chelper_cwd': (),
        'link_klippy_tree': False,
    },
    'klipper': {
        'install_dir': ('klippy', 'extras'),
        'create_package_marker': False,
        'chelper_code': 'import chelper; chelper.get_ffi()',
        'chelper_cwd': ('klippy',),
        'link_klippy_tree': True,
    },
}

# Klipper's gcode dispatcher catches CommandError and nothing else, so any
# other exception is logged with a traceback and shuts the printer down.
# Whatever the case expects, none of these may appear.
FORBIDDEN_ALWAYS = (
    'Traceback (most recent call last)',
    'Unhandled exception',
    'Internal error',
    'hotendchanger internal error',
)

# klippy/util.py (_try_read_file): the startup probe of
# /proc/device-tree/model catches IOError/OSError, logs it at debug level
# with a full traceback under this prefix, and klippy continues. On hosts
# without that file the traceback is routine, so it alone must not trip
# FORBIDDEN_ALWAYS.
BENIGN_TRACEBACK_PREFIX = 'Exception on read /proc/device-tree/model:'


def strip_benign_traceback(output):
    lines = output.splitlines(keepends=True)
    kept = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith(BENIGN_TRACEBACK_PREFIX):
            kept.append(lines[i])
            i += 1
            continue
        i += 1
        while i < len(lines) and lines[i][:1] in (' ', '\t'):
            i += 1
        if i < len(lines) and lines[i].startswith('FileNotFoundError'):
            i += 1
    return ''.join(kept)


# klippy logs this once every config section has been built and the connect
# handlers are running, so it is what says the plugin's sections survived
# config load. The gcode of a case runs only after it.
CONNECTED = "Sending MCU 'mcu' printer configuration..."

NOT_HOMED = 'Home all axes with G28 before a tool change'

# A config-error case fails during connect; klippy logs that error with a
# traceback (klippy/printer.py logging.exception in _connect), so those
# cases list the traceback marker under 'allow' instead of tripping
# FORBIDDEN_ALWAYS.
CONFIG_ERROR_TRACEBACK = ('Traceback (most recent call last)',)

CASES = (
    {
        'name': 'config load',
        'test': 'config_load.test',
        'require': (CONNECTED, 'active_tool: none', 'state: unknown'),
        'forbid': ('Unknown command:"HOTENDCHANGER_STATUS"',),
    },
    {
        'name': 'toolchange',
        'test': 'toolchange.test',
        'require': (
            'active_tool: T1',
            'active_tool: T0',
            'hotendchanger before: old=none new=T1',
            'hotendchanger after: old=T1 new=T0',
            'Activating extruder extruder1',
            # The trailing newline keeps this marker from matching inside
            # the extruder1 line above.
            'Activating extruder extruder\n',
            'hotendchanger: waiting for extruder1 to reach 60.0C'
            ' (within 2.0C)',
            'gcode homing: X:0.400000 Y:0.200000 Z:-0.050000',
            'gcode homing: X:0.000000 Y:0.000000 Z:0.000000',
            'stepper motion_queue: extruder1',
            'stepper motion_queue: extruder\n',
        ),
        'forbid': (
            'Unknown command:"T1"',
            'Unknown command:"T0"',
            'is not a valid extruder',
        ),
    },
    {
        'name': 'T1 unhomed',
        'test': 'toolchange_unhomed.test',
        'require': (NOT_HOMED,),
        'forbid': ('Unknown command:"T1"',),
    },
    {
        'name': 'SET_TOOL_OFFSET',
        'test': 'set_tool_offset.test',
        'require': (
            'T1 offset: X=0.100000 Y=0.000000 Z=0.000000',
            'T1 offset stored for SAVE_CONFIG',
            'gcode homing: X:0.100000 Y:0.000000 Z:0.000000',
        ),
        'forbid': ('Unknown command:"SET_TOOL_OFFSET"',),
    },
    {
        'name': 'SET_TOOL_OFFSET bad tool',
        'test': 'set_tool_offset_bad_tool.test',
        'require': ('no hotendchanger_tool T9 configured',),
        'forbid': ('Unknown command:"SET_TOOL_OFFSET"',),
    },
    {
        'name': 'bad tool numbering',
        'test': 'bad_numbering.test',
        'require': ('must be numbered T0..T1',),
        'forbid': (),
        'allow': CONFIG_ERROR_TRACEBACK,
    },
    {
        'name': 'missing extruder section',
        'test': 'missing_extruder.test',
        'require': ("names extruder section 'extruder9' which does not exist",),
        'forbid': (),
        'allow': CONFIG_ERROR_TRACEBACK,
    },
    {
        'name': 'gcode_macro collision',
        'test': 'macro_collision.test',
        'require': (
            'cannot register gcode command T1 for [hotendchanger_tool T1]',),
        'forbid': (),
        'allow': CONFIG_ERROR_TRACEBACK,
    },
    {
        'name': 'mixed detect_pin',
        'test': 'mixed_detect.test',
        'require': (
            'either every hotendchanger_tool must set detect_pin or none may;'
            ' missing on: T0',),
        'forbid': (),
        'allow': CONFIG_ERROR_TRACEBACK,
    },
    {
        'name': 'duplicate extruder',
        'test': 'duplicate_extruder.test',
        'require': ("both name extruder section 'extruder'",),
        'forbid': (),
        'allow': CONFIG_ERROR_TRACEBACK,
    },
)


class Failure(Exception):
    pass


def report(line=''):
    sys.stdout.write(line + '\n')
    sys.stdout.flush()


def check_module_lists():
    """The module list exists twice by design: install.sh must stay a
    dependency-free shell script. This check keeps the two lists identical."""
    install_sh = REPO_DIR / 'install.sh'
    text = install_sh.read_text(errors='replace')
    match = re.search(r'^PLUGIN_FILES="([^"]*)"', text, re.MULTILINE)
    if match is None:
        raise Failure(
            "%s no longer defines PLUGIN_FILES; keep its module list and "
            "this script's PLUGIN_MODULES identical." % (install_sh,))
    sh_modules = tuple(match.group(1).split())
    if sh_modules != PLUGIN_MODULES:
        raise Failure(
            "module lists diverge: install.sh PLUGIN_FILES=%r, "
            "integration_test.py PLUGIN_MODULES=%r. Register every plugin "
            "module in both." % (sh_modules, PLUGIN_MODULES))


def check_checkout(raw):
    checkout = Path(raw).expanduser().resolve()
    if not checkout.is_dir():
        raise Failure(
            "Firmware checkout not found: %s. Pass the directory holding "
            "the firmware's klippy/ and scripts/ directories." % (checkout,))
    needed = [
        checkout / 'klippy',
        checkout / 'scripts' / 'test_klippy.py',
        checkout / 'test' / 'configs' / ('%s.config' % (MCU_TARGET,)),
        checkout / 'Makefile',
    ]
    missing = [str(p) for p in needed if not p.exists()]
    if missing:
        raise Failure(
            "%s does not look like a Kalico or Klipper checkout. Missing: %s."
            % (checkout, ", ".join(missing)))
    return checkout


def detect_layout(checkout):
    """Name the firmware layout of a checkout, from the LAYOUTS closed set."""
    # Kalico's module loader is klippy/printer.py scanning "klippy.plugins."
    # (printer.py:282-286); the plugins directory itself may not exist yet in
    # a fresh checkout. Stock Klipper has no printer.py and
    # klippy/klippy.py:93-103 loads only from klippy/extras/.
    printer_py = checkout / 'klippy' / 'printer.py'
    if (printer_py.is_file()
            and 'klippy.plugins' in printer_py.read_text(errors='replace')):
        return 'kalico'
    if ((checkout / 'klippy' / 'klippy.py').is_file()
            and (checkout / 'klippy' / 'extras').is_dir()):
        return 'klipper'
    raise Failure(
        "%s is neither a Kalico nor a stock Klipper checkout: it has no "
        "klippy/printer.py loading klippy.plugins, and no klippy/klippy.py "
        "with a klippy/extras/ directory." % (checkout,))


def run_tool(command, cwd, env, what):
    try:
        proc = subprocess.run(
            command, cwd=str(cwd), env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as e:
        raise Failure("%s could not be started: %s." % (what, e))
    if proc.returncode != 0:
        raise Failure(
            "%s failed with exit code %d.\n%s"
            % (what, proc.returncode, proc.stdout))
    return proc.stdout


def build_dictionary(checkout, build_dir, dictdir):
    """Build the MCU dictionary the cases run against from this checkout.

    The dictionary carries the command set of the firmware, so it has to come
    from the same checkout as klippy: a dictionary built elsewhere would test
    the plugin against a protocol that checkout does not speak.
    """
    source = checkout / 'test' / 'configs' / ('%s.config' % (MCU_TARGET,))
    build_dir.mkdir(parents=True, exist_ok=True)
    config = build_dir / '.config'
    shutil.copyfile(str(source), str(config))
    # The Makefile joins OUT with relative paths, so it has to end in a
    # separator, and it keeps the build out of the checkout's own out/.
    make = [
        'make', '-C', str(checkout),
        'OUT=%s%s' % (build_dir, os.sep),
        'KCONFIG_CONFIG=%s' % (config,),
    ]
    env = os.environ.copy()
    try:
        run_tool(make + ['olddefconfig'], checkout, env,
                 'The firmware configure')
        run_tool(make, checkout, env, 'The firmware build')
    except Failure as e:
        raise Failure(
            "%s\nThe %s firmware is built with make and the host C compiler. "
            "Install both, or point --dictdir at a directory that already "
            "holds %s." % (e, MCU_TARGET, DICT_NAME))
    built = build_dir / 'klipper.dict'
    if not built.is_file():
        raise Failure(
            "The firmware build produced no %s. The build writes the "
            "dictionary next to klipper.elf; check the build output above."
            % (built,))
    dictdir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(built), str(dictdir / DICT_NAME))


def build_chelper(checkout, env, layout):
    """Compile klippy's C helper before any case runs.

    Kalico's own test/conftest.py calls chelper.get_ffi() at session start for
    the same reason: the first import compiles c_helper.so, and a compiler or
    dependency problem there would otherwise be reported as a failing case.
    """
    spec = LAYOUTS[layout]
    try:
        run_tool(
            [sys.executable, '-c', spec['chelper_code']],
            checkout.joinpath(*spec['chelper_cwd']), env,
            "Building klippy's C helper")
    except Failure as e:
        raise Failure(
            "%s\nklippy needs a C compiler and its Python dependencies "
            "(cffi, greenlet, Jinja2, numpy, pyserial). Install them into "
            "%s, the interpreter running this script."
            % (e, sys.executable))


def link_plugin(source, target):
    try:
        os.symlink(str(source), str(target))
        return 'symlink'
    except OSError as symlink_error:
        try:
            shutil.copyfile(str(source), str(target))
        except OSError as copy_error:
            raise Failure(
                "Could not install the plugin at %s. The symlink failed with "
                "%s and the copy failed with %s. Check the write permissions "
                "on that directory." % (target, symlink_error, copy_error))
        return 'copy'


@contextlib.contextmanager
def installed_plugin(checkout, layout):
    spec = LAYOUTS[layout]
    plugins_dir = checkout.joinpath(*spec['install_dir'])
    package_marker = plugins_dir / '__init__.py'
    created_dir = created_marker = False
    if not plugins_dir.is_dir():
        plugins_dir.mkdir(parents=True)
        created_dir = True
    if spec['create_package_marker'] and not package_marker.exists():
        package_marker.touch()
        created_marker = True
    installed = []
    backups = []
    try:
        for source in PLUGIN_SRCS:
            target = plugins_dir / source.name
            if target.is_symlink() or target.exists():
                if target.resolve() == source:
                    report('plugin: already installed at %s' % (target,))
                    continue
                backup = plugins_dir / (target.name + '.integration-backup')
                if backup.exists():
                    raise Failure(
                        "%s already exists. A previous run left it behind: "
                        "move the file you want to keep back to %s and "
                        "delete the other one." % (backup, target))
                os.replace(str(target), str(backup))
                backups.append((backup, target))
            report('plugin: installed at %s by %s'
                   % (target, link_plugin(source, target)))
            installed.append(target)
        yield
    finally:
        for target in installed:
            if target.is_symlink() or target.exists():
                target.unlink()
        for backup, target in backups:
            os.replace(str(backup), str(target))
        cache = plugins_dir / '__pycache__'
        if cache.is_dir():
            shutil.rmtree(str(cache), ignore_errors=True)
        if created_marker and package_marker.exists():
            package_marker.unlink()
        if created_dir:
            try:
                plugins_dir.rmdir()
            except OSError as e:
                report('note: %s was created by this run and could not be '
                       'removed: %s' % (plugins_dir, e))


def case_output(case_dir, harness_output):
    """Everything one case produced: what the firmware's harness printed,
    plus the klippy log it kept. That harness prints the log itself when a
    run defied the case's expectation, so it is appended only when it is not
    there yet.
    """
    # On a timeout subprocess hands back bytes even in text mode
    # (TimeoutExpired.output is never decoded), so the markers would
    # otherwise be compared against bytes and raise instead of reporting.
    if isinstance(harness_output, bytes):
        harness_output = harness_output.decode('utf-8', errors='replace')
    output = harness_output or ''
    log_path = case_dir / KLIPPY_LOG
    if not log_path.is_file():
        return output + '\n%s was never written.\n' % (log_path,)
    log = log_path.read_text(errors='replace')
    if log in output:
        return output
    return output + log


def stage_cases(scratch):
    """Copy the cases where they can sit next to the printer.cfg copy."""
    staged = scratch / 'cases'
    staged.mkdir()
    for case in CASES:
        shutil.copyfile(str(CASE_DIR / case['test']),
                        str(staged / case['test']))
    for config in sorted(CASE_DIR.glob('*.cfg')):
        shutil.copyfile(str(config), str(staged / config.name))
    return staged


def run_case(case, checkout, staged, dictdir, workdir, env, layout):
    """Run one case through the firmware's harness. Returns (problems,
    output)."""
    # klippy appends to its log file and the name above is the same for every
    # case, so each case runs in a directory of its own.
    case_dir = workdir / case['test']
    case_dir.mkdir(parents=True, exist_ok=True)
    if LAYOUTS[layout]['link_klippy_tree']:
        tree_link = case_dir / 'klippy'
        if not tree_link.exists():
            try:
                os.symlink(str(checkout / 'klippy'), str(tree_link))
            except OSError as e:
                return (["the klippy tree could not be linked into %s: %s"
                         % (case_dir, e)], '')
    command = [
        sys.executable, str(checkout / 'scripts' / 'test_klippy.py'),
        '-k', '-d', str(dictdir), '-t', '.', str(staged / case['test']),
    ]
    try:
        proc = subprocess.run(
            command, cwd=str(case_dir), env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=CASE_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        return (["the run did not finish within %.0f seconds"
                 % (CASE_TIMEOUT,)], case_output(case_dir, e.output))
    except OSError as e:
        return (["test_klippy.py could not be started: %s" % (e,)], '')
    output = case_output(case_dir, proc.stdout)
    checked = strip_benign_traceback(output)
    problems = []
    if proc.returncode != 0:
        problems.append(
            "test_klippy.py reported failure, exit code %d"
            % (proc.returncode,))
    for marker in case['require']:
        if marker not in output:
            problems.append("the expected output %r never appeared" % (marker,))
    allowed = tuple(case.get('allow', ()))
    forbidden = tuple(case['forbid']) + tuple(
        m for m in FORBIDDEN_ALWAYS if m not in allowed)
    for marker in forbidden:
        if marker in checked:
            problems.append("the output %r appeared" % (marker,))
    return problems, output


def run_cases(checkout, staged, dictdir, workdir, env, verbose, layout):
    failed = 0
    for case in CASES:
        problems, output = run_case(
            case, checkout, staged, dictdir, workdir, env, layout)
        if problems:
            failed += 1
            report('case %s: FAILED' % (case['name'],))
            for problem in problems:
                report('    %s' % (problem,))
        else:
            report('case %s: passed' % (case['name'],))
        if problems or verbose:
            report('--- output of %s ---' % (case['test'],))
            report(output.rstrip())
            report('--- end of output ---')
    report()
    report('cases run: %d' % (len(CASES),))
    report('cases failed: %d' % (failed,))
    return failed


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Run the plugin's config sections and its commands through the "
            "firmware's own regression harness against a Kalico or stock "
            "Klipper checkout. This is a separate entry point from the unit "
            "tests, because it needs a checkout, a C compiler and a POSIX "
            "host."))
    parser.add_argument(
        'checkout',
        help="path to the firmware checkout to test the plugin against")
    parser.add_argument(
        '--dictdir', default=None,
        help=("directory holding %s. It is built from the checkout when it is "
              "missing there. Without this option the dictionary is built into "
              "a temporary directory on every run." % (DICT_NAME,)))
    parser.add_argument(
        '--verbose', action='store_true',
        help="print the klippy output of every case, not only failing ones")
    args = parser.parse_args()

    if os.name != 'posix':
        raise Failure(
            "klippy runs on POSIX hosts only: it needs fork and the termios "
            "module. Run this script on the printer host, or in a Linux "
            "virtual machine or container with the Kalico checkout mounted.")
    for source in PLUGIN_SRCS:
        if not source.is_file():
            raise Failure(
                "cannot find %s. Run this script from its own repository."
                % (source,))
    check_module_lists()

    checkout = check_checkout(args.checkout)
    layout = detect_layout(checkout)
    report('firmware checkout: %s' % (checkout,))
    report('firmware layout: %s' % (layout,))
    report('python: %s' % (sys.executable,))

    env = os.environ.copy()
    existing = env.get('PYTHONPATH')
    env['PYTHONPATH'] = (
        str(checkout) if not existing
        else str(checkout) + os.pathsep + existing)

    with tempfile.TemporaryDirectory(
            prefix='hotendchanger-integration-') as scratch:
        scratch = Path(scratch)
        if args.dictdir is None:
            dictdir = scratch / 'dict'
        else:
            dictdir = Path(args.dictdir).expanduser().resolve()
        if (dictdir / DICT_NAME).is_file():
            report('dictionary: %s, reused' % (dictdir / DICT_NAME,))
        else:
            build_dictionary(checkout, scratch / 'build', dictdir)
            report('dictionary: %s, built from this checkout'
                   % (dictdir / DICT_NAME,))
        build_chelper(checkout, env, layout)
        staged = stage_cases(scratch)
        workdir = scratch / 'run'
        workdir.mkdir()
        report()
        with installed_plugin(checkout, layout):
            failed = run_cases(
                checkout, staged, dictdir, workdir, env, args.verbose, layout)
    return 1 if failed else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Failure as error:
        sys.stderr.write('integration_test.py: %s\n' % (error,))
        sys.exit(2)
