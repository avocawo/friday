import json
import subprocess
import unittest
from unittest.mock import patch

from friday import vcs
from friday.errors import FridayError

GITLAB = {"gitlab": {"base_url": "https://gitlab.example", "project": "team/app"}}
GITHUB = {"github": {"repository": "owner/app"}}


def completed(value, code=0):
    return subprocess.CompletedProcess([], code, json.dumps(value), "")


class VcsTests(unittest.TestCase):
    def test_gitlab_draft_is_created_with_raw_fields_once(self):
        created = {"iid": 5, "web_url": "https://gitlab.example/team/app/-/merge_requests/5", "source_branch": "friday/T-1",
                   "target_branch": "develop"}
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            return completed([] if "GET" in args else created)
        with patch("friday.vcs.subprocess.run", side_effect=run):
            result = vcs.open_draft(GITLAB, "gitlab", "friday/T-1", "develop", "T-1: title", "body")
        self.assertEqual(result, {"iid": 5, "url": created["web_url"]})
        post = calls[1]
        self.assertEqual(post[:7], ["glab", "api", "--hostname", "gitlab.example", "--method", "POST", "projects/team%2Fapp/merge_requests"])
        self.assertIn("title=Draft: T-1: title", post)
        with patch("friday.vcs.subprocess.run", return_value=completed([{"iid": 5, "web_url": "u", "source_branch": "friday/T-1"}])) as run:
            self.assertEqual(vcs.open_draft(GITLAB, "gitlab", "friday/T-1", "develop", "t", "b"), {"iid": 5, "url": "u"})
        self.assertEqual(run.call_count, 1)

    def test_unconfirmed_creation_is_an_error(self):
        with patch("friday.vcs.subprocess.run", side_effect=[completed([]), completed({}, 1)]), self.assertRaises(FridayError) as error:
            vcs.open_draft(GITLAB, "gitlab", "b", "t", "x", "y")
        self.assertEqual(error.exception.code, "delivery_unknown")
        with self.assertRaises(FridayError):
            vcs.open_draft({"gitlab": {"base_url": "http://plain", "project": "p"}}, "gitlab", "b", "t", "x", "y")

    def test_github_draft_pull_request(self):
        response = {"number": 9, "html_url": "https://github.com/owner/app/pull/9", "head": {"ref": "friday/T-1"},
                    "base": {"ref": "main"}, "title": "Draft: t", "draft": True}
        with patch("friday.vcs.subprocess.run", side_effect=[completed([]), completed(response)]) as run:
            result = vcs.open_draft(GITHUB, "github", "friday/T-1", "main", "t", "b")
        self.assertEqual((result["iid"], result["url"]), (9, "https://github.com/owner/app/pull/9"))
        payload = json.loads(run.call_args.kwargs["input"])
        self.assertEqual((payload["draft"], payload["head"], payload["base"]), (True, "friday/T-1", "main"))


if __name__ == "__main__":
    unittest.main()
