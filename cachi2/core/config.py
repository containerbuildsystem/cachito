config = None

class Config:
    cachito_athens_url = None
    cachito_default_environment_variables = {
        "gomod": {"GOSUMDB": {"value": "off", "kind": "literal"}},
    }
    cachito_gomod_ignore_missing_gomod_file = False
    cachito_gomod_download_max_tries = 5
    cachito_gomod_file_deps_allowlist = {}
    cachito_gomod_strict_vendor = False
    cachito_subprocess_timeout = 3600

# This function is kept to avoid changing the old code too much
# It should be removed with the refactoring of the config object
def get_worker_config():
    return Config()
