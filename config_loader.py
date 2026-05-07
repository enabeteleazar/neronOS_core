import yaml


CONFIG_PATH = "/etc/neron/neron.yaml"


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


config = load_config()
