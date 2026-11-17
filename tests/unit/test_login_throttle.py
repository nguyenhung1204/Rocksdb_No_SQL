import unittest

from webapp.login_throttle import LoginThrottle


class LoginThrottleTest(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.throttle = LoginThrottle(
            account_limit=2, client_limit=4, window_seconds=10,
            cooldown_seconds=5, max_entries=3, clock=lambda: self.now[0],
        )

    def test_account_cooldown_expires_and_success_clears_account_state(self):
        self.throttle.record_failure("admin", "127.0.0.1")
        self.throttle.record_failure("admin", "127.0.0.1")
        self.assertEqual(self.throttle.retry_after("admin", "127.0.0.1"), 5)

        self.now[0] += 6
        self.assertEqual(self.throttle.retry_after("admin", "127.0.0.1"), 0)
        self.throttle.record_failure("admin", "127.0.0.1")
        self.assertEqual(self.throttle.retry_after("admin", "127.0.0.1"), 0)
        self.throttle.clear_account("admin")
        self.assertEqual(self.throttle.retry_after("admin", "127.0.0.1"), 0)

    def test_client_limit_is_shared_across_accounts(self):
        for username in ("one", "two", "three", "four"):
            self.throttle.record_failure(username, "192.0.2.4")
        self.assertGreater(self.throttle.retry_after("another", "192.0.2.4"), 0)

    def test_entries_remain_bounded_for_attacker_chosen_usernames_and_addresses(self):
        for index in range(30):
            self.throttle.record_failure("user-%d" % index, "192.0.2.%d" % index)
        self.assertLessEqual(self.throttle.size, 3)

    def test_expired_failure_window_resets_without_permanent_lockout(self):
        self.throttle.record_failure("admin", "127.0.0.1")
        self.throttle.record_failure("admin", "127.0.0.1")
        self.now[0] += 11
        self.assertEqual(self.throttle.retry_after("admin", "127.0.0.1"), 0)
        self.throttle.record_failure("admin", "127.0.0.1")
        self.assertEqual(self.throttle.retry_after("admin", "127.0.0.1"), 0)
