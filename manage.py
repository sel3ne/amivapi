import codecs
import datetime as dt
from os import mkdir, urandom
from os.path import abspath, dirname, join, exists, expanduser
from pprint import pprint
from base64 import b64encode

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError

from flask import Flask
from flask.ext.script import (
    Manager,
    prompt,
    prompt_bool,
    prompt_choices,
    prompt_pass,
)

from amivapi import settings, models, schemas, cron
from amivapi.models import User
from amivapi.utils import init_database, create_new_hash, get_config


manager = Manager(Flask("amivapi"))


"""

Utility functions

"""


def config_path(environment):
    return join(settings.ROOT_DIR, "config", "%s.cfg" % environment)


def load_config(environment):
    if environment is None:
        print("Please specify an environment using -c.")
        exit(0)

    return get_config(environment)


def save_config(name, **config):
    config_dir = dirname(name)
    if not exists(config_dir):
        mkdir(config_dir, 0700)

    with codecs.open(name, "w", encoding="utf-8") as f:
        f.write('"""Automatically generated on %s"""\n\n'
                % dt.datetime.utcnow())

        for key, value in sorted(config.items()):
            if key not in dir(settings) or value != getattr(settings, key):
                f.write("%s = %r\n" % (key.upper(), value))

"""

API Key management

"""


def change_apikey(environment, permissions):
    resources = schemas.get_domain().keys()

    while True:
        print("Change apikey:")
        pprint(permissions)

        i = 1
        for res in resources:
            print(str(i) + ": " + res)
            i += 1
        print("s: save")
        options = map(lambda i: str(i + 1), range(len(resources))) + ['s']
        choice = prompt_choices("Choose resource to change",
                                options,
                                "s")
        if choice == "s":
            return
        resource = resources[int(choice) - 1]

        method = prompt_choices("Choose method to toggle",
                                ['get', 'post', 'patch', 'put', 'delete'])

        if resource not in permissions \
                or method not in permissions[resource] \
                or not permissions[resource][method]:
            if resource not in permissions:
                permissions[resource] = {}
            permissions[resource][method] = 1
        else:
            del permissions[resource][method]
            if len(permissions[resource]) == 0:
                del permissions[resource]

        print("\n")


@manager.option("-c", "--config", dest="environment", required=True)
def apikeys_add(environment):
    """ Adds a new API key """

    cfg = load_config(environment)
    target_file = config_path(environment)

    name = prompt("What purpose does the key have?")
    newkey = b64encode(urandom(512))

    if "APIKEYS" not in cfg:
        cfg['APIKEYS'] = {}
    permissions = cfg['APIKEYS'][newkey] = {"name": name}
    change_apikey(environment, permissions)

    save_config(target_file, **cfg)
    print("\nSaved key %s:\n" % name)
    print(newkey)


@manager.option("-c", "--config", dest="environment", required=True)
def apikeys_list(environment):

    cfg = load_config(environment)

    for token, entry in cfg['APIKEYS'].iteritems():
        name = entry['name']
        print("=== %s ===\n\n%s\n" % (name, token))


def choose_apikey(environment, cfg):
    keys = cfg['APIKEYS'].keys()
    names = map(lambda entry: entry['name'], cfg['APIKEYS'].values())

    i = 1
    for n in names:
        print("%i: %s" % (i, n))
        i += 1

    options = map(lambda i: str(i + 1), range(len(names)))
    choice = prompt_choices("Choose API key to change", options)

    return keys[int(choice) - 1]


@manager.option("-c", "--config", dest="environment", required=True)
def apikeys_edit(environment):
    cfg = load_config(environment)
    target_file = config_path(environment)

    key = choose_apikey(environment, cfg)

    permissions = cfg['APIKEYS'][key]
    change_apikey(environment, permissions)

    save_config(target_file, **cfg)
    print("Saved key: %s" % permissions['name'])


@manager.option("-c", "--config", dest="environment", required=True)
def apikeys_delete(environment):
    cfg = load_config(environment)
    target_file = config_path(environment)

    key = choose_apikey(environment, cfg)
    sure = prompt_choices("Are you sure?", ["y", "n"], "n")

    if sure == "y":
        del cfg['APIKEYS'][key]
        save_config(target_file, **cfg)

"""

Create new config

"""


@manager.command
def create_config():
    """Creates a new configuration for an environment.

    The config file is stored in the ROOT/config directory.
    """
    environment = prompt_choices("Choose the environment the config is for",
                                 ["development", "testing", "production"],
                                 default="development")

    target_file = config_path(environment)
    if exists(target_file):
        if not prompt_bool("The file config/%s.cfg already exists, "
                           "do you want to overwrite it?" % environment):
            return

    # Configuration values
    config = {
        'DEBUG': environment == "development",
        'TESTING': environment == "testing",
    }

    # Ask for database settings
    db_uri = None
    db_type = prompt_choices("Choose the type of database",
                             ["sqlite", "mysql"],
                             default="sqlite")
    if db_type == "sqlite":
        db_filepath = prompt("Path to db file (including filename)",
                             default=join(settings.ROOT_DIR, "data.db"))
        db_uri = "sqlite:///%s" % abspath(expanduser(db_filepath))

    elif db_type == "mysql":
        user = prompt("MySQL username", default="amivapi")
        pw = prompt_pass("MySQL password")
        host = prompt("MySQL host", default="localhost")
        db = prompt("MySQL database", default="amivapi")

        db_uri = "mysql://%s:%s@%s/%s?charset=utf8" % (user, pw, host, db)

    config['SQLALCHEMY_DATABASE_URI'] = db_uri

    file_dir = abspath(expanduser(prompt("Path to file storage folder",
                                         default=join(settings.ROOT_DIR,
                                                      "filestorage"))))

    config['STORAGE_DIR'] = file_dir

    config['ROOT_MAIL'] = prompt("Maintainer E-Mail")

    config['SMTP_SERVER'] = prompt("SMTP Server to send mails",
                                   default='localhost')

    forward_dir = prompt("Directory where forwards are stored",
                         default=join(settings.ROOT_DIR, "forwards"))
    config['FORWARD_DIR'] = abspath(expanduser(forward_dir))

    if not exists(file_dir):
        mkdir(file_dir, 0700)

    if not exists(config['FORWARD_DIR']):
        mkdir(config['FORWARD_DIR'], 0700)

    config['APIKEYS'] = {}

    # Write everything to file
    # Note: The file is opened in non-binary mode, because we want python to
    # auto-convert newline characters to the underlying platform.
    save_config(target_file, **config)

    if environment != "testing":
        engine = create_engine(config['SQLALCHEMY_DATABASE_URI'])
        try:
            print("Setting up database...")
            init_database(engine, config)
        except OperationalError:
            if not prompt_bool(
                    "A database seems to exist already. Overwrite it?(y/N)"):
                return
            models.Base.metadata.drop_all(engine)
            init_database(engine, config)


"""
    Run cron tasks
"""


@manager.option("-c", "--config", dest="environment", required=True)
def run_cron(environment):
    """ Run tasks like sending notifications and cleaning the database """

    cfg = get_config(environment)

    engine = create_engine(cfg['SQLALCHEMY_DATABASE_URI'])
    sessionmak = sessionmaker(bind=engine)
    session = sessionmak()

    cron.run(session, cfg)


"""

Change root password

"""


@manager.option("-c", "--config", dest="environment", required=True)
def set_root_password(environment=None):
    """Sets the root password. """

    cfg = get_config(environment)

    engine = create_engine(cfg['SQLALCHEMY_DATABASE_URI'])
    sessionmak = sessionmaker(bind=engine)
    session = sessionmak()

    try:
        root = session.query(User).filter(User.id == 0).one()
    except OperationalError:
        print ("No root user found, please recreate the config to set up a"
               " database.")
        exit(0)

    root.password = create_new_hash(prompt("New root password"))

    session.commit()
    session.close()


"""

Run the FlaskScript manager

"""


if __name__ == "__main__":
    manager.run()
