# The firmware resolves a [hotendchanger_tool Tn] section to a module named
# hotendchanger_tool (first word of the section name: Kalico
# klippy/printer.py:282-286, stock Klipper klippy/klippy.py:93-103), so this
# module forwards those sections to the tool loader. Kalico imports plugins
# under the klippy.plugins package; stock Klipper imports extras as an
# absolute "extras" package (klippy.py:103), so the sibling import tries the
# package present in the running firmware.
try:
    from klippy.plugins import hotendchanger
except ImportError:
    from extras import hotendchanger


def load_config_prefix(config):
    return hotendchanger.load_config_prefix(config)
