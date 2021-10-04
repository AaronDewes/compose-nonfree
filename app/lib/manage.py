#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2021 Aaron Dewes <aaron.dewes@protonmail.com>
#
# SPDX-License-Identifier: MIT

import stat
import threading
from typing import List
from sys import argv
import os
import requests
import shutil
import json
import yaml
import subprocess

from lib.composegenerator.v0.generate import createComposeConfigFromV0
from lib.composegenerator.v1.generate import createComposeConfigFromV1
from lib.appymlgenerator import convertComposeYMLToAppYML
from lib.validate import findAndValidateApps
from lib.metadata import getAppRegistry, getSimpleAppRegistry

# For an array of threads, join them and wait for them to finish


def joinThreads(threads: List[threading.Thread]):
    for thread in threads:
        thread.join()


# The directory with this script
scriptDir = os.path.dirname(os.path.realpath(__file__))
nodeRoot = os.path.join(scriptDir, "..", "..")
appsDir = os.path.join(nodeRoot, "apps")
appDataDir = os.path.join(nodeRoot, "app-data")
userFile = os.path.join(nodeRoot, "db", "user.json")
legacyScript = os.path.join(nodeRoot, "scripts", "app")


def runCompose(app: str, args: str):
    # If the app is neither squeaknode nor samourai-server, run compose(app, args) instead
    if app != "squeaknode" and app != "samourai-server":
        compose(app, args)
    os.system("{script} compose {app} {args}".format(
        script=legacyScript, app=app, args=args))

# Returns a list of every argument after the second one in sys.argv joined into a string by spaces


def getArguments():
    arguments = ""
    for i in range(3, len(argv)):
        arguments += argv[i] + " "
    return arguments


def getAppYml(name):
    url = 'https://raw.githubusercontent.com/runcitadel/compose-nonfree/main/apps/' + \
        name + '/' + 'app.yml'
    response = requests.get(url)
    if response.status_code == 200:
        return response.text
    else:
        return False


def getAppYmlPath(app):
    return os.path.join(appsDir, app, 'app.yml')


def composeToAppYml(app):
    composeFile = os.path.join(appsDir, app, "docker-compose.yml")
    appYml = os.path.join(appsDir, app, "app.yml")
    # Read the compose file and parse it
    with open(composeFile, "r") as f:
        compose = yaml.safe_load(f)
    registry = os.path.join(appsDir, "registry.json")
    # Load the registry
    with open(registry, "r") as f:
        registryData = json.load(f)
    converted = convertComposeYMLToAppYML(compose, app, registryData)
    # Put converted into the app.yml after encoding it as YAML
    with open(appYml, "w") as f:
        f.write(yaml.dump(converted, sort_keys=False))


def update(verbose: bool = False):
    apps = findAndValidateApps(appsDir)
    # The compose generation process updates the registry, so we need to get it set up with the basics before that
    registry = getAppRegistry(apps, appsDir)
    with open(os.path.join(appsDir, "registry.json"), "w") as f:
        json.dump(registry, f, indent=4, sort_keys=True)
    print("Wrote registry to registry.json")

    simpleRegistry = getSimpleAppRegistry(apps, appsDir)
    with open(os.path.join(appsDir, "apps.json"), "w") as f:
        json.dump(simpleRegistry, f, indent=4, sort_keys=True)
    print("Wrote version information to apps.json")

    # Loop through the apps and generate valid compose files from them, then put these into the app dir
    for app in apps:
        composeFile = os.path.join(appsDir, app, "docker-compose.yml")
        appYml = os.path.join(appsDir, app, "app.yml")
        with open(composeFile, "w") as f:
            appCompose = getApp(appYml, app)
            if(appCompose):
                f.write(yaml.dump(appCompose, sort_keys=False))
                if verbose:
                    print("Wrote " + app + " to " + composeFile)
    print("Generated configuration successfully")


def download(app: str = None):
    if(app is None):
        apps = findAndValidateApps(appsDir)
        for app in apps:
            data = getAppYml(app)
            if data:
                with open(getAppYmlPath(app), 'w') as f:
                    f.write(data)
            else:
                print("Warning: Could not download " + app)
    else:
        data = getAppYml(app)
        if data:
            with open(getAppYmlPath(app), 'w') as f:
                f.write(data)
        else:
            print("Warning: Could not download " + app)


def getUserData():
    userData = {}
    if os.path.isfile(userFile):
        with open(userFile, "r") as f:
            userData = json.load(f)
    return userData


def startInstalled():
    # If userfile doen't exist, just do nothing
    userData = {}
    if os.path.isfile(userFile):
        with open(userFile, "r") as f:
            userData = json.load(f)
    threads = []
    for app in userData["installedApps"]:
        print("Starting app {}...".format(app))
        # Run runCompose(args.app, "up --detach") asynchrounously for all apps, then exit(0) when all are finished
        thread = threading.Thread(target=runCompose, args=(app, "up --detach"))
        thread.start()
        threads.append(thread)
    joinThreads(threads)


def stopInstalled():
    # If userfile doen't exist, just do nothing
    userData = {}
    if os.path.isfile(userFile):
        with open(userFile, "r") as f:
            userData = json.load(f)
    threads = []
    for app in userData["installedApps"]:
        print("Stopping app {}...".format(app))
        # Run runCompose(args.app, "up --detach") asynchrounously for all apps, then exit(0) when all are finished
        thread = threading.Thread(
            target=runCompose, args=(app, "rm --force --stop"))
        thread.start()
        threads.append(thread)
    joinThreads(threads)

# Loads an app.yml and converts it to a docker-compose.yml


def getApp(appFile: str, appId: str):
    with open(appFile, 'r') as f:
        app = yaml.safe_load(f)

    if not "metadata" in app:
        raise Exception("Error: Could not find metadata in " + appFile)
    app["metadata"]["id"] = appId

    if('version' in app and str(app['version']) == "1"):
        return createComposeConfigFromV1(app, nodeRoot)
    else:
        return createComposeConfigFromV0(app)


def compose(app, arguments):
    # Runs a compose command in the app dir
    # Before that, check if a docker-compose.yml exists in the app dir
    composeFile = os.path.join(appsDir, app, "docker-compose.yml")
    commonComposeFile = os.path.join(appsDir, "docker-compose.common.yml")
    os.environ["APP_DOMAIN"] = subprocess.check_output("hostname -s 2>/dev/null || echo 'umbrel'", shell=True).decode("utf-8") + ".local"
    os.environ["APP_HIDDEN_SERVICE"] = subprocess.check_output("cat {} 2>/dev/null || echo 'notyetset.onion'".format(os.path.join(nodeRoot, "app-{}/hostname".format(app))), shell=True).decode("utf-8")
    os.environ["APP_SEED"] = deriveEntropy("app-{}-seed".format(app))
    os.environ["APP_DATA_DIR"] = os.path.join(appsDir, app, "data")
    os.environ["BITCOIN_DATA_DIR"] = os.path.join(nodeRoot, "bitcoin")
    os.environ["LND_DATA_DIR"] = os.path.join(nodeRoot, "lnd")
    if not os.path.isfile(composeFile):
        print("Error: Could not find docker-compose.yml in " + app)
        exit(1)
    os.system(
        "docker compose --env-file '{}' --project-name '{}' --file '{}' --file '{}' {}".format(
            os.path.join(nodeRoot, ".env"), app, commonComposeFile, composeFile, arguments))


def remove_readonly(func, path, _):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def deleteData(app: str):
    dataDir = os.path.join(appDataDir, app)
    try:
        shutil.rmtree(dataDir, onerror=remove_readonly)
    except FileNotFoundError:
        pass


def createDataDir(app: str):
    dataDir = os.path.join(appDataDir, app)
    appDir = os.path.join(appsDir, app)
    if os.path.isdir(dataDir):
        deleteData(app)
    # Recursively copy everything from appDir to dataDir while excluding .gitignore
    shutil.copytree(appDir, dataDir, symlinks=False,
                    ignore=shutil.ignore_patterns(".gitignore"))
    # Chown and chmod dataDir to have the same owner and permissions as appDir
    os.chown(dataDir, os.stat(appDir).st_uid, os.stat(appDir).st_gid)
    os.chmod(dataDir, os.stat(appDir).st_mode)


def setInstalled(app: str):
    userData = getUserData()
    if not "installedApps" in userData:
        userData["installedApps"] = []
    userData["installedApps"].append(app)
    userData["installedApps"] = list(set(userData["installedApps"]))
    with open(userFile, "w") as f:
        json.dump(userData, f)


def setRemoved(app: str):
    userData = getUserData()
    if not "installedApps" in userData:
        return
    userData["installedApps"] = list(set(userData["installedApps"]))
    userData["installedApps"].remove(app)
    with open(userFile, "w") as f:
        json.dump(userData, f)


def deriveEntropy(identifier: str):
    seedFile = os.path.join(nodeRoot, "db", "umbrel-seed", "seed")
    alternativeSeedFile = os.path.join(nodeRoot, "db", "umbrel-seed", "seed")
    if not os.path.isfile(seedFile):
        if(os.path.isfile(alternativeSeedFile)):
            seedFile = alternativeSeedFile
        else:
            print("No seed file found, exiting...")
            exit(1)
    with open(seedFile, "r") as f:
        umbrel_seed = f.read().strip()
    entropy = subprocess.check_output(
        'printf "%s" "{}" | openssl dgst -sha256 -binary -hmac "{}" | xxd -p | tr --delete "\n"'.format(identifier, umbrel_seed), shell=True)
    return entropy.decode("utf-8")
