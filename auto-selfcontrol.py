#!/usr/bin/python

import subprocess
import os
import json
import time
from datetime import datetime
import plistlib
import logging.handlers
import traceback
import sys
import re
from Foundation import NSUserDefaults, CFPreferencesSetAppValue, CFPreferencesAppSynchronize, NSDate
from pwd import getpwnam
from optparse import OptionParser

SETTINGS_DIR = os.path.expanduser("~") + '/.config/auto-selfcontrol'

# Configure global logger
LOGGER = logging.getLogger("Auto-SelfControl")
LOGGER.setLevel(logging.INFO)
handler = logging.handlers.SysLogHandler('/var/run/syslog')
handler.setFormatter(logging.Formatter(
    '%(name)s: [%(levelname)s] %(message)s'))
LOGGER.addHandler(handler)

def load_config(path):
    """Load a JSON configuration file"""
    config = dict()

    try:
        with open(path, 'rt') as cfg:
            config.update(json.load(cfg))
    except ValueError as exception:
        exit_with_error("The JSON config file {configfile} is not correctly formatted."
                        "The following exception was raised:\
                        \n{exc}".format(configfile=path, exc=exception))

    return config

def run(settings_dir):
    """Load config and start SelfControl"""
    run_config = "{path}/run_config.json".format(path=settings_dir)
    if not os.path.exists(run_config):
        exit_with_error(
            "Run config file could not be found in installation location, please make sure that you have Auto-SelfControl activated/installed")

    config = load_config(run_config)

    """Start SelfControl with custom parameters, depending on the weekday and the config"""

    if check_if_running(config):
        print("SelfControl is already running, exit")
        LOGGER.error(
            "SelfControl is already running, ignore current execution of Auto-SelfControl.")
        exit(2)

    try:
        schedule = next(
            s for s in config["block-schedules"] if is_schedule_active(s))
    except StopIteration:
        print("No Schedule is active at the moment.")
        LOGGER.warn("No schedule is active at the moment. Shutting down.")
        exit(0)

    block_end_date = get_end_date_of_schedule(schedule)
    blocklist_path = "{settings}/blocklist".format(settings=settings_dir)

    update_blocklist(blocklist_path, config, schedule)

    # Start SelfControl
    execSelfControl(config, ["--install", blocklist_path, block_end_date])

    LOGGER.info("SelfControl started until {end} minute(s).".format(
        end=block_end_date))


def get_selfcontrol_out_pattern(content_pattern):
    """Returns a RegEx pattern that matches SelfControl's output with the provided content_pattern"""
    return r'^.*org\.eyebeam\.SelfControl[^ ]+\s*' + content_pattern + r'\s*$'


def check_if_running(config):
    """Check if SelfControl is already running."""
    output = execSelfControl(config, ["--is-running"]).decode('UTF-8')
    m = re.search(
        get_selfcontrol_out_pattern(r'(NO|YES)'), output, re.MULTILINE)
    if m is None:
        exit_with_error("Could not detect if SelfControl is running.")
    return m.groups()[0] != 'NO'


def is_schedule_active(schedule):
    """Check if we are right now in the provided schedule or not."""
    currenttime = datetime.today()
    starttime = datetime(currenttime.year, currenttime.month, currenttime.day, schedule["start-hour"],
                         schedule["start-minute"])
    endtime = datetime(currenttime.year, currenttime.month, currenttime.day, schedule["end-hour"],
                       schedule["end-minute"])
    d = endtime - starttime

    for weekday in get_schedule_weekdays(schedule):
        weekday_diff = currenttime.isoweekday() % 7 - weekday % 7

        if weekday_diff == 0:
            # schedule's weekday is today
            result = starttime <= currenttime and endtime >= currenttime if d.days == 0 else starttime <= currenttime
        elif weekday_diff == 1 or weekday_diff == -6:
            # schedule's weekday was yesterday
            result = d.days != 0 and currenttime <= endtime
        else:
            # schedule's weekday was on any other day.
            result = False

        if result:
            return result

    return False

def get_end_date_of_schedule(schedule):
    """Return the end date of the provided schedule in ISO 8601 format"""
    currenttime = datetime.today()
    endtime = datetime(
        currenttime.year, currenttime.month, currenttime.day, schedule['end-hour'], schedule['end-minute'])
    # manually create ISO8601 string because of tz issues with Python2
    ts = time.time()
    utc_offset = ((datetime.fromtimestamp(
        ts) - datetime.utcfromtimestamp(ts)).total_seconds()) / 3600
    offset = str(int(abs(utc_offset * 100))).zfill(4)
    sign = "+" if utc_offset >= 0 else "-"

    return endtime.strftime("%Y-%m-%dT%H:%M:%S{sign}{offset}".format(sign=sign, offset=offset))


def get_schedule_weekdays(schedule):
    """Return a list of weekdays the specified schedule is active."""
    return [schedule["weekday"]] if schedule.get("weekday", None) is not None else range(1, 8)


def get_launchscript(config, settings_dir):
    """Return the string of the launchscript."""
    return '''<?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
        <key>Label</key>
        <string>com.parrot-bytes.auto-selfcontrol</string>
        <key>ProgramArguments</key>
        <array>
            <string>/usr/bin/python</string>
            <string>{path}</string>
            <string>--run</string>
            <string>--dir</string>
            <string>{dir}</string>
        </array>
        <key>StartCalendarInterval</key>
        <array>
            {startintervals}</array>
        <key>RunAtLoad</key>
        <true/>
    </dict>
    </plist>'''.format(path=os.path.realpath(__file__), startintervals="".join(get_launchscript_startintervals(config)), dir=settings_dir)


def get_launchscript_startintervals(config):
    """Return the string of the launchscript start intervals."""
    for schedule in config["block-schedules"]:
        for weekday in get_schedule_weekdays(schedule):
            yield '''<dict>
                    <key>Weekday</key>
                    <integer>{weekday}</integer>
                    <key>Minute</key>
                    <integer>{startminute}</integer>
                    <key>Hour</key>
                    <integer>{starthour}</integer>
                </dict>
                '''.format(weekday=weekday, startminute=schedule['start-minute'], starthour=schedule['start-hour'])


def execSelfControl(config, arguments):
    user_id = str(getpwnam(config["username"]).pw_uid)
    output = subprocess.check_output(
        ["{path}/Contents/MacOS/org.eyebeam.SelfControl".format(
            path=config["selfcontrol-path"]), user_id] + arguments,
        stderr=subprocess.STDOUT
    )
    return output


def install(config, settings_dir):
    """ installs auto-selfcontrol """
    print("> Start installation of Auto-SelfControl")

    launchplist_path = "/Library/LaunchDaemons/com.parrot-bytes.auto-selfcontrol.plist"

    # Check for existing plist
    if os.path.exists(launchplist_path):
        print("> Removed previous installation files")
        subprocess.call(["launchctl", "unload", "-w", launchplist_path])
        os.unlink(launchplist_path)

    launchplist_script = get_launchscript(config, settings_dir)

    with open(launchplist_path, 'w') as myfile:
        myfile.write(launchplist_script)

    subprocess.call(["launchctl", "load", "-w", launchplist_path])

    print("> Save run configuration")
    if not os.path.exists(settings_dir):
        os.makedirs(settings_dir)

    with open("{dir}/run_config.json".format(dir=settings_dir), 'w') as fp:
        fp.write(json.dumps(config))

    print("> Installed\n")


def check_config(config):
    """ checks whether the config file is correct """
    if "username" not in config:
        exit_with_error("No username specified in config.")
    if config["username"].encode('UTF-8') not in get_osx_usernames():
        exit_with_error(
            "Username '{username}' unknown.\nPlease use your OSX username instead.\n"
            "If you have trouble finding it, just enter the command 'whoami'\n"
            "in your terminal.".format(
                username=config["username"]))
    if "selfcontrol-path" not in config:
        exit_with_error(
            "The setting 'selfcontrol-path' is required and must point to the location of SelfControl.")
    if not os.path.exists(config["selfcontrol-path"]):
        exit_with_error(
            "The setting 'selfcontrol-path' does not point to the correct location of SelfControl. "
            "Please make sure to use an absolute path and include the '.app' extension, "
            "e.g. /Applications/SelfControl.app")
    if "block-schedules" not in config:
        exit_with_error("The setting 'block-schedules' is required.")
    if len(config["block-schedules"]) == 0:
        exit_with_error("You need at least one schedule in 'block-schedules'.")
    if config.get("host-blacklist", None) is None:
        print("WARNING:")
        msg = "It is not recommended to directly use SelfControl's blacklist. Please use the 'host-blacklist' " \
              "setting instead."
        print(msg)
        LOGGER.warn(msg)


def update_blocklist(blocklist_path, config, schedule):
    """Save the blocklist with the current configuration"""
    plist = {
        "HostBlacklist": config["host-blacklist"],
        "BlockAsWhitelist": schedule.get("block-as-whitelist", False)
    }
    with open(blocklist_path, 'wb') as fp:
        plistlib.dump(plist, fp)


def get_osx_usernames():
    output = subprocess.check_output(["dscl", ".", "list", "/users"])
    return [s.strip() for s in output.splitlines()]


def excepthook(excType, excValue, tb):
    """ This function is called whenever an exception is not caught. """
    err = "Uncaught exception:\n{}\n{}\n{}".format(str(excType), excValue,
                                                   "".join(traceback.format_exception(excType, excValue, tb)))
    LOGGER.error(err)
    print(err)


def exit_with_error(message):
    LOGGER.error(message)
    print("ERROR:")
    print(message)
    exit(1)


if __name__ == "__main__":
    sys.excepthook = excepthook

    if os.geteuid() != 0:
        exit_with_error("Please make sure to run the script with elevated \
                         rights, such as:\nsudo python {file} \
                         ".format(file=os.path.realpath(__file__)))

    PARSER = OptionParser()
    PARSER.add_option("-r", "--run", action="store_true",
                      dest="run", default=False)
    PARSER.add_option("-i", "--install", action="store_true",
                      dest="install", default=False)
    PARSER.add_option("-d", "--dir", action="store",
                      dest="dir", default=SETTINGS_DIR)
    (OPTS, ARGS) = PARSER.parse_args()

    if OPTS.run:
        run(OPTS.dir)
    elif OPTS.install:
        CONFIG_FILE = "{path}/config.json".format(path=OPTS.dir)
        if not os.path.exists(CONFIG_FILE):
            exit_with_error(
                "There was no config file found in {dir}, please create a config file.".format(dir=OPTS.dir))

        CONFIG = load_config(CONFIG_FILE)
        check_config(CONFIG)

        install(CONFIG, OPTS.dir)
        schedule_is_active = any(
            s for s in CONFIG["block-schedules"] if is_schedule_active(s))

        if schedule_is_active and not check_if_running(CONFIG):
            print("> Active schedule found for SelfControl!")
            print("> Start SelfControl (this could take a few minutes)\n")
            run(OPTS.dir)
            print("\n> SelfControl was started.\n")
    else:
        exit_with_error(
            "No action specified")
