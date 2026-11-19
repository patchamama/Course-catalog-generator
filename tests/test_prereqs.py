"""System-prerequisite planner (tools/prereqs.py): the commands per platform, without installing anything.

Run:  python -m unittest tests.test_prereqs
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
import prereqs as P  # noqa: E402


def flat(cmds):
    return [' '.join(c) for c in cmds]


class InstallCommandTests(unittest.TestCase):
    def test_macos_uses_homebrew_without_sudo(self):
        self.assertEqual(flat(P.install_commands('docker', 'mac', 'brew')), ['brew install --cask docker'])
        self.assertEqual(flat(P.install_commands('ollama', 'mac', 'brew')), ['brew install ollama'])
        self.assertEqual(flat(P.install_commands('vlc', 'mac', 'brew')), ['brew install --cask vlc'])
        for comp in P.COMPONENTS:
            for cmd in P.install_commands(comp, 'mac', 'brew'):
                self.assertNotIn('sudo', cmd, f'{comp}: Homebrew must never run under sudo')

    def test_ubuntu_uses_apt_with_sudo(self):
        docker = flat(P.install_commands('docker', 'linux', 'apt', sudo=['sudo']))
        self.assertEqual(docker[0], 'sudo apt-get update')
        self.assertIn('sudo apt-get install -y docker.io', docker)
        self.assertTrue(any('docker-compose-v2' in c and 'docker-compose-plugin' in c for c in docker), 'compose package fallbacks')
        self.assertTrue(any(c.startswith('sudo systemctl enable --now docker') for c in docker))
        self.assertTrue(any('usermod -aG docker' in c for c in docker))
        self.assertEqual(flat(P.install_commands('python-venv', 'linux', 'apt', sudo=['sudo']))[1],
                         'sudo apt-get install -y python3-venv python3-pip')
        self.assertEqual(flat(P.install_commands('ffmpeg', 'linux', 'apt', sudo=['sudo'])), ['sudo apt-get install -y ffmpeg'])

    def test_root_needs_no_sudo(self):
        self.assertEqual(flat(P.install_commands('pandoc', 'linux', 'apt', sudo=[])), ['apt-get install -y pandoc'])

    def test_windows_uses_winget_with_unattended_flags(self):
        for comp, pkg in (('docker', 'Docker.DockerDesktop'), ('ollama', 'Ollama.Ollama'), ('node', 'OpenJS.NodeJS.LTS'),
                          ('pandoc', 'JohnMacFarlane.Pandoc'), ('ffmpeg', 'Gyan.FFmpeg')):
            (cmd,) = flat(P.install_commands(comp, 'windows', 'winget'))
            self.assertIn(f'winget install -e --id {pkg}', cmd)
            self.assertIn('--accept-package-agreements', cmd)
            self.assertIn('--silent', cmd)

    def test_other_linux_package_managers(self):
        self.assertIn('dnf install -y', flat(P.install_commands('node', 'linux', 'dnf', sudo=[]))[0])
        self.assertIn('pacman -S --noconfirm', flat(P.install_commands('ffmpeg', 'linux', 'pacman', sudo=[]))[0])

    def test_unknown_things_give_no_commands(self):
        self.assertEqual(P.install_commands('docker', 'mac', None), [])
        self.assertEqual(P.install_commands('nope', 'linux', 'apt'), [])
        self.assertEqual(P.install_commands('python-venv', 'mac', 'brew'), [])   # only Debian/Ubuntu split it out

    def test_every_component_is_installable_on_every_platform(self):
        for os_name, pm in (('mac', 'brew'), ('linux', 'apt'), ('windows', 'winget')):
            for comp in P.COMPONENTS:
                with self.subTest(os=os_name, component=comp):
                    self.assertTrue(P.install_commands(comp, os_name, pm), 'no install recipe')


class PlanTests(unittest.TestCase):
    def test_package_manager_detection(self):
        def have(names):
            return lambda c: c in names
        self.assertEqual(P.package_manager('mac', have({'brew'})), 'brew')
        self.assertIsNone(P.package_manager('mac', have(set())))
        self.assertEqual(P.package_manager('linux', have({'apt-get', 'dnf'})), 'apt')
        self.assertEqual(P.package_manager('linux', have({'dnf'})), 'dnf')
        self.assertEqual(P.package_manager('windows', have({'winget'})), 'winget')
        self.assertIsNone(P.package_manager('windows', have(set())))

    def test_plan_lists_only_missing_and_respects_extras(self):
        rows = P.plan('linux', False, lambda c: c in ('docker', 'node'), assume_pm=True)
        by = {r['component']: r for r in rows}
        self.assertTrue(by['docker']['present'])
        self.assertEqual(by['docker']['commands'], [])
        self.assertFalse(by['ollama']['present'])
        self.assertTrue(by['ollama']['commands'])
        self.assertNotIn('vlc', by)
        self.assertIn('vlc', {r['component'] for r in P.plan('linux', True, lambda c: False, assume_pm=True)})

    def test_wanted_groups(self):
        self.assertEqual(set(P.wanted(False)), {'docker', 'ollama', 'node', 'pandoc', 'ffmpeg'})
        self.assertEqual(set(P.wanted(True)), {'docker', 'ollama', 'node', 'pandoc', 'ffmpeg', 'vlc', 'tesseract'})


if __name__ == '__main__':
    unittest.main()
