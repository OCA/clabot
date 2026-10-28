from cla.tasks import Author, commit_signer


def _user(login, id):
    return {"login": login, "id": id, "node_id": f"N_{id}"}


def _commit(author, author_email, committer, committer_email):
    return {
        "author": author,
        "committer": committer,
        "commit": {
            "author": {"name": "Author Name", "email": author_email},
            "committer": {"name": "Committer Name", "email": committer_email},
        },
    }


class TestCommitSigner:
    def test_author_is_committer(self):
        commit = _commit(_user("alice", 1), "alice@a.com", _user("alice", 1), "alice@a.com")
        assert commit_signer(commit) == Author("alice", 1, "N_1", "alice@a.com")

    def test_committer_differs_from_author(self):
        commit = _commit(_user("alice", 1), "alice@old.com", _user("bob", 2), "bob@b.com")
        assert commit_signer(commit) == Author("bob", 2, "N_2", "bob@b.com")

    def test_same_github_account_different_emails(self):
        commit = _commit(_user("alice", 1), "alice@old.com", _user("alice", 1), "alice@new.com")
        assert commit_signer(commit) == Author("alice", 1, "N_1", "alice@old.com")

    def test_web_flow_committer(self):
        commit = _commit(
            _user("alice", 1), "alice@a.com", _user("web-flow", 19864447), "noreply@github.com"
        )
        assert commit_signer(commit) == Author("alice", 1, "N_1", "alice@a.com")

    def test_unlinked_accounts_same_email(self):
        commit = _commit(None, "Alice@a.com", None, "alice@a.com")
        assert commit_signer(commit) == Author("Author Name", None, None, "Alice@a.com")

    def test_unlinked_author_linked_committer(self):
        commit = _commit(None, "alice@old.com", _user("bob", 2), "bob@b.com")
        assert commit_signer(commit) == Author("bob", 2, "N_2", "bob@b.com")

    def test_unlinked_committer(self):
        commit = _commit(_user("alice", 1), "alice@a.com", None, "bob@b.com")
        assert commit_signer(commit) == Author("Committer Name", None, None, "bob@b.com")
