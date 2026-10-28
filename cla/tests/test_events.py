import asyncio
from collections import namedtuple
from unittest import mock

import pytest

from cla import events
from cla.events import handle_issue_comment, is_recheck_command

Event = namedtuple("Event", "data")


@pytest.mark.parametrize(
    "body",
    [
        "/clabot recheck",
        "/CLABot Recheck",
        "  /clabot recheck please",
        "I signed it.\n/clabot recheck",
    ],
)
def test_is_recheck_command(body):
    assert is_recheck_command(body)


@pytest.mark.parametrize(
    "body",
    [
        None,
        "",
        "clabot recheck",
        "Please run /clabot recheck",
        "/clabot rechecking",
        "/ocabot rebase",
    ],
)
def test_is_not_recheck_command(body):
    assert not is_recheck_command(body)


def _event(body="/clabot recheck", is_pull_request=True, sender_type="User"):
    issue = {"number": 7}
    if is_pull_request:
        issue["pull_request"] = {}
    return Event(
        {
            "issue": issue,
            "comment": {"id": 42, "body": body},
            "repository": {"id": 1, "full_name": "OCA/repo"},
            "sender": {"type": sender_type},
        }
    )


def _run(event):
    gh = mock.AsyncMock()
    gh.getitem.return_value = {"number": 7}
    with mock.patch.object(events, "handle_pull_request", mock.AsyncMock()) as handle_pr:
        asyncio.run(handle_issue_comment(event, gh))
    return gh, handle_pr


def test_recheck_command_rechecks_pull_request():
    gh, handle_pr = _run(_event())
    gh.getitem.assert_awaited_once_with("/repos/OCA/repo/pulls/7")
    handle_pr.assert_awaited_once()
    assert handle_pr.await_args.args[0].data == {
        "pull_request": {"number": 7},
        "repository": {"id": 1, "full_name": "OCA/repo"},
    }
    gh.post.assert_awaited_once_with(
        "/repos/OCA/repo/issues/comments/42/reactions", data={"content": "+1"}
    )


@pytest.mark.parametrize(
    "event",
    [
        _event(body="LGTM"),
        _event(is_pull_request=False),
        _event(sender_type="Bot"),
    ],
)
def test_ignored_comments(event):
    gh, handle_pr = _run(event)
    handle_pr.assert_not_awaited()
    gh.post.assert_not_awaited()
