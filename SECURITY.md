# Security checklist for production

1. Replace every development/default secret.
2. Run the API behind HTTPS and a trusted reverse proxy.
3. Move database access from the development SQLite repository to PostgreSQL.
4. Enable refresh-token/session revocation and device/session management.
5. Add real OTP/email verification before activating accounts when required.
6. Configure a concrete payment gateway adapter and verify webhooks according to its official documentation.
7. Store PDFs/images in private object storage and issue time-limited access URLs for protected resources.
8. Add rate limiting, request-size limits, audit logs and structured error logging.
9. Back up PostgreSQL and test restores regularly.
10. Restrict admin roles using least privilege and require strong credentials/2FA for privileged accounts.
