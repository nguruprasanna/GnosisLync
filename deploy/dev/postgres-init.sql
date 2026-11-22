-- Dev only. The application connects as gnosis_api, a member of gnosis_app (no BYPASSRLS, owns nothing).
CREATE ROLE gnosis_app NOLOGIN NOBYPASSRLS;
CREATE ROLE gnosis_api LOGIN PASSWORD 'dev-only' IN ROLE gnosis_app;
