# Kalico resolves a [hotendchanger_tool Tn] section to a module named
# hotendchanger_tool (klippy/printer.py:282-286 takes the first word of the
# section name), so this module forwards those sections to the tool loader.
from klippy.plugins import hotendchanger


def load_config_prefix(config):
    return hotendchanger.load_config_prefix(config)
