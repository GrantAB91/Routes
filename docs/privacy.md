# Privacy

## What is stored

| Data | Retention |
| --- | --- |
| Account email, display name, Argon2id password hash | Until deletion |
| Routes, versions, waypoints, avoid areas | Until deleted by the owner |
| Uploaded files, unmodified | Until the import is deleted |
| External account tokens, encrypted | Until disconnected or revoked |
| Request logs with request id | 30 days |

## Defaults

New routes are **private**. Publication, sharing and unlisting are explicit acts
by the owner. Nothing is public by default and nothing becomes public as a side
effect of another action.

## What is never logged

- Precise private route geometry
- Account tokens or passwords
- Uploaded file contents

Structured logs carry a request id, a route id and metric counts. They do not
carry coordinates.

## Rights

- **Disconnect an account** — revokes upstream first, then removes the record.
- **Export your data** — routes, versions and imports in their original formats.
- **Delete a route** — removes it and its versions.
- **Delete your account** — recorded as `deletion_requested_at` and completed
  asynchronously across routes, imports, exports and object storage, so the work
  is auditable rather than a single destructive write.

## Location

Contour asks for device location only when the rider uses "start from my
location", and explains why at the point of asking. Location is never sent to a
third party; geocoding runs against a self-hosted instance or is disabled.

## Not yet implemented

- The account deletion worker is designed but not built; the request is recorded
  and no automated erasure runs yet.
- No data processing agreement or retention policy has been reviewed by anyone
  qualified to review it.
