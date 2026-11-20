import base64
import json
import unittest
from unittest.mock import patch

from friday import isolated_preview
from friday.errors import FridayError


class PreviewTests(unittest.TestCase):
    def archive(self, files):
        import io
        import tarfile
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w') as tar:
            for name, data in files.items():
                item = tarfile.TarInfo(name)
                item.size = len(data)
                tar.addfile(item, io.BytesIO(data))
        return buffer.getvalue()

    def test_storybook_links_the_kit_snapshot_and_runs_without_network(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            showcase = Path(directory) / 'storybook'
            kit = showcase / 'deps' / 'vue-uikit'
            kit.mkdir(parents=True)
            # An example profile: everything project-specific (command, paths, environment) is the profile's.
            profile = {'browser': {'mode': 'local'},
                       'preview': {'backend': 'storybook', 'port': 6006, 'env': {'NODE_ENV': 'development'},
                                   'command': ['sh', '-c', 'ln -s /src/{mount} /app/node_modules/@scope/kit && exec serve -p 6006'],
                                   'tmpfs': ['/app/node_modules/@scope'], 'links': {'node_modules': '/app/node_modules'}},
                       'stories': {'repository': '/r', 'base_branch': 'master', 'worktree_root': '/w', 'image': 'sb',
                                   'mount': 'deps/vue-uikit'}}
            archives = {str(showcase): self.archive({'.storybook/main.ts': b'x', 'src/stories/Button.stories.ts': b'y'}),
                        str(kit): self.archive({'src/index.ts': b'z'})}
            seen = {}
            def snapshot(selected):
                return 'h', 'd', archives[selected['repository']]
            def docker(*args, **kwargs):
                if args[0] == 'run':
                    source = Path(args[args.index('--mount') + 1].split(',')[1][4:])
                    seen['files'] = sorted(str(path.relative_to(source)) for path in source.rglob('*') if path.is_file())
                    seen['node_modules'] = str((source / 'node_modules').readlink()) if hasattr(Path, 'readlink') else '/app/node_modules'
                return b''
            with patch('friday.isolated_preview.gate.image_id', return_value='sha') as image, \
                 patch('friday.isolated_preview.gate.snapshot', side_effect=snapshot), \
                 patch('friday.isolated_preview.wait_for_server') as wait, \
                 patch('friday.isolated_preview.docker', side_effect=docker) as calls:
                with isolated_preview.open_preview(profile, str(kit)) as url:
                    self.assertRegex(url, r'^http://preview-[0-9a-f]+\.localhost:\d+/$')
            image.assert_called_once_with('sb')
            self.assertEqual(wait.call_args.args[1:], (6006,))
            self.assertEqual(seen['files'], ['.storybook/main.ts', 'deps/vue-uikit/src/index.ts', 'src/stories/Button.stories.ts'])
            self.assertEqual(seen['node_modules'], '/app/node_modules')
            run = calls.call_args_list[0].args
            self.assertEqual(run[run.index('--network') + 1], 'none')
            self.assertIn('--read-only', run)
            self.assertIn('/app/node_modules/@scope:rw,exec,nosuid,nodev,size=512m', run)
            self.assertIn('NODE_ENV=development', run)
            self.assertEqual(run[run.index('--entrypoint') + 1], 'sh')
            self.assertEqual(run[-1], 'ln -s /src/deps/vue-uikit /app/node_modules/@scope/kit && exec serve -p 6006')
            for broken in ({'command': 'serve'}, {'port': None}, {'tmpfs': ['relative']}, {'links': {'../x': '/a'}}):
                with self.assertRaises(FridayError):
                    isolated_preview.validate_storybook(dict(profile['preview'], **broken))
            outside = Path(directory) / 'elsewhere'
            outside.mkdir()
            with self.assertRaises(FridayError):
                with isolated_preview.open_preview(profile, str(outside)):
                    pass

    def test_vite_preview_in_local_mode_has_no_staging_api(self):
        profile = {'browser': {'mode': 'local'}, 'gate_image': 'img', 'preview': {'backend': 'docker-vite'}}
        with patch('friday.isolated_preview.gate.image_id', return_value='sha'), \
             patch('friday.isolated_preview.gate.snapshot', return_value=('h', 'd', self.archive({'index.html': b'<html>'}))), \
             patch('friday.isolated_preview.wait_for_server'), \
             patch('friday.isolated_preview.docker', return_value=b'') as docker:
            with isolated_preview.open_preview(profile, '/worktree'):
                pass
        run = docker.call_args_list[0].args
        self.assertFalse(any(str(arg).startswith('VITE_API_APOLLO_URI') for arg in run))
        self.assertEqual(run[-4:], ('--host', '127.0.0.1', '--port', '5173'))
        with self.assertRaises(FridayError):  # a staging browser still needs its API path
            with isolated_preview.open_preview({'browser': {'stage_origin': 'https://gql.stage.example.ru'},
                                                'preview': {'backend': 'docker-vite'}}, '/w'):
                pass

    def test_readiness_retries_until_successful_html_response(self):
        def response(status, content_type, body):
            return json.dumps({'status': status, 'type': content_type, 'data': base64.b64encode(body).decode()}).encode()
        values = [b'not-json', response(404, 'text/html', b'<html>not found</html>'),
                  response(500, 'text/html', b'<html>error</html>'),
                  response(200, 'application/json', b'<html></html>'),
                  response(200, 'text/html', b'not html'),
                  response(200, 'text/html; charset=utf-8', b'<!doctype html><html></html>')]
        with patch('friday.isolated_preview.docker', side_effect=values) as docker, \
             patch('friday.isolated_preview.time.sleep'):
            isolated_preview.wait_for_server('synthetic', 5173)
        self.assertEqual(docker.call_count, len(values))
        self.assertEqual(docker.call_args.args[-2:], ('/', '5173'))

    def test_readiness_fails_closed_after_bounded_retries(self):
        cases = [b'{}', b'[]', b'{"status":200,"type":"text/html","data":"%%%"}',
                 json.dumps({'status': 500, 'type': 'text/html', 'data': 'PGh0bWw+'}).encode()]
        for value in cases:
            with self.subTest(value=value), patch('friday.isolated_preview.docker', return_value=value) as docker, \
                 patch('friday.isolated_preview.time.sleep'), self.assertRaises(FridayError) as error:
                isolated_preview.wait_for_server('synthetic', 5173)
            self.assertEqual(error.exception.code, 'preview_unavailable')
            self.assertEqual(docker.call_count, 40)


class ServerTests(unittest.TestCase):
    def test_docker_vite_expands_to_the_generic_server(self):
        run = isolated_preview.server({'backend': 'docker-vite', 'api_path': '/gql'}, 'https://api.stage.example')
        self.assertEqual((run['port'], run['env']['VITE_API_APOLLO_URI'], run['links']),
                         (5173, 'https://api.stage.example/gql', {'node_modules': '/app/node_modules'}))
        self.assertEqual(isolated_preview.browser_spec({'browser': {}, 'preview': {'backend': 'docker-vite'}})['stage_check'],
                         isolated_preview.VITE_STAGE_CHECK)
        self.assertNotIn('stage_check', isolated_preview.browser_spec({'browser': {}, 'preview': {'backend': 'docker-staging'}}))

    def test_any_dev_server_runs_with_the_staging_origin_in_its_environment(self):
        run = isolated_preview.server({'backend': 'docker-staging', 'command': ['python3', '-m', 'app', '--port', '8000'], 'port': 8000,
                                       'env': {'API_URL': '{stage_origin}/api'}, 'links': {'.venv': '/opt/venv'}},
                                      'https://api.staging.example')
        self.assertEqual((run['command'][0], run['env'], run['links']), ('python3', {'API_URL': 'https://api.staging.example/api'},
                                                                          {'.venv': '/opt/venv'}))
        for bad in ({'command': [], 'port': 8000}, {'command': ['x'], 'port': 80}, {'command': ['x'], 'port': 8000, 'env': {'A B': 'x'}},
                    {'command': ['x'], 'port': 8000, 'links': {'../x': '/y'}}):
            with self.assertRaises(FridayError):
                isolated_preview.server(dict(bad, backend='docker-staging'), 'https://api.staging.example')
