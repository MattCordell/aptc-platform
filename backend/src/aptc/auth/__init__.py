"""OIDC token verification and the permission framework (NFR-07, FR-44, NFR-20,
FR-01, FR-80, FR-81, NFR-06). `aptc.auth.permissions` holds the PRD Section 4.7
matrix as code, `aptc.auth.principal` the resolved-actor type,
`aptc.auth.authorisation` the check API and `aptc.auth.grants` role granting and
revoking. `aptc.auth.identity` maps a verified identity to an `app_user`."""
