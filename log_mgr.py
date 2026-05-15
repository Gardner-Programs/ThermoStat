import builtins
import os

_LOG_FILE   = 'system.log'
_MAX_BYTES  = 10000   # trim when file exceeds ~10KB
_KEEP_LINES = 150     # lines to retain after trim

_orig_print = builtins.print


def _log_print(*args, **kwargs):
    _orig_print(*args, **kwargs)
    try:
        sep  = kwargs.get('sep', ' ')
        end  = kwargs.get('end', '\n')
        line = sep.join([str(a) for a in args]) + end
        with open(_LOG_FILE, 'a') as f:
            f.write(line)
        if os.stat(_LOG_FILE)[6] > _MAX_BYTES:
            _trim()
    except:
        pass


def _trim():
    try:
        _TMP = _LOG_FILE + '.tmp'
        # Count lines without loading them all into RAM
        total = 0
        with open(_LOG_FILE, 'r') as f:
            for _ in f:
                total += 1
        if total <= _KEEP_LINES:
            return
        # Copy only the last _KEEP_LINES lines to a temp file, then replace
        skip = total - _KEEP_LINES
        n = 0
        with open(_LOG_FILE, 'r') as src, open(_TMP, 'w') as dst:
            for line in src:
                n += 1
                if n > skip:
                    dst.write(line)
        os.remove(_LOG_FILE)
        os.rename(_TMP, _LOG_FILE)
    except:
        pass


def setup():
    """Override builtins.print so every print() in the codebase is also saved to system.log."""
    builtins.print = _log_print
