"""Prepare and run Axiom locally: python3 run_local.py [--setup-only]."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent
NODE_VERSION = '22.22.3'


def run(*args, env=None, cwd=ROOT):
    subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, check=True)


def compatible_node(executable):
    if not executable:
        return False
    try:
        version = subprocess.check_output([str(executable), '--version'], text=True).strip()
        major, minor, patch = map(int, version.lstrip('v').split('.'))
        return (major == 22 and (minor, patch) >= (22, 3)) or (major >= 24 and (major, minor, patch) >= (24, 15, 0))
    except (OSError, ValueError, subprocess.CalledProcessError):
        return False


def prepare_frontend():
    env = os.environ.copy()
    npm = shutil.which('npm')
    if not npm:
        raise RuntimeError('Install Node.js with npm, then run this command again.')
    if not compatible_node(shutil.which('node')):
        managed = ROOT / '.tools' / 'node'
        binary = managed / 'node_modules' / 'node' / ('bin/node' if os.name != 'nt' else 'bin/node.exe')
        if not compatible_node(binary):
            print(f'Installing project-local Node {NODE_VERSION}; system Node will stay unchanged.', flush=True)
            run(npm, 'install', '--prefix', managed, '--no-save', '--package-lock=false', '--no-audit', '--no-fund', f'node@{NODE_VERSION}')
        if not compatible_node(binary):
            raise RuntimeError(f'Could not prepare Node {NODE_VERSION}. Install a compatible Node version and retry.')
        env['PATH'] = str(binary.parent) + os.pathsep + env.get('PATH', '')
    frontend = ROOT / 'frontend'
    lock_hash = hashlib.sha256((frontend / 'package-lock.json').read_bytes() + (frontend / 'package.json').read_bytes()).hexdigest()
    stamp = frontend / 'node_modules' / '.axiom-install'
    if not stamp.exists() or stamp.read_text() != lock_hash:
        run(npm, 'ci', '--no-audit', '--no-fund', env=env, cwd=frontend)
        stamp.write_text(lock_hash)
    # Always rebuild so source changes and newly added routes are included.
    run(npm, 'run', 'build', env=env, cwd=frontend)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup-only', action='store_true', help='Install and build without starting the server')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise RuntimeError('Python 3.11 or later is required.')
    if not 1 <= args.port <= 65535:
        raise RuntimeError('Choose a port between 1 and 65535.')
    os.chdir(ROOT)
    if not args.setup_only:
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', args.port))
            except OSError as exc:
                raise RuntimeError(f'Port {args.port} is already in use. Stop that server or pass --port with another port.') from exc
    environment = ROOT / '.venv'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        print('Creating .venv…', flush=True)
        venv.EnvBuilder(with_pip=True).create(environment)
    run(python, '-m', 'pip', 'install', '-r', ROOT / 'requirements.txt')
    run(python, '-m', 'pip', 'check')
    if not (ROOT / '.env').exists():
        # Exclusive creation protects credentials even if another setup runs concurrently.
        try:
            with (ROOT / '.env').open('x') as output:
                output.write((ROOT / '.env.example').read_text())
            print('Created .env from the example. Connect Kite through Profile when ready.', flush=True)
        except FileExistsError:
            pass
    prepare_frontend()
    if args.setup_only:
        print('Local setup complete. Run python3 run_local.py to start.')
        return
    env = os.environ.copy()
    env.update(AUTO_START_TRADING='0', RESTORE_TRADING_SESSIONS='1', APP_MODE='PAPER',
               DASHBOARD_HOST='127.0.0.1', DASHBOARD_PORT=str(args.port), PORT=str(args.port))
    print(f'Open http://127.0.0.1:{args.port} — engine auto-start is disabled. Ctrl+C stops the server.', flush=True)
    os.execve(str(python), [str(python), str(ROOT / 'main.py')], env)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nLocal startup stopped.')
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f'Local startup failed: {exc}', file=sys.stderr)
        sys.exit(1)
