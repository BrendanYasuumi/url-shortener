# URL Shortener and Analytics API

A RESTful backend service for creating compact URLs, redirecting visitors, and
tracking link usage.

## Project scope

The completed service will provide:

- URL submission with HTTP/HTTPS validation;
- unique six-character short codes using Base62 encoding;
- fast, indexed short-code lookups in SQLite;
- temporary redirects to original URLs;
- click totals and creation timestamps through an analytics endpoint;
- automated API and concurrency tests; and
- a portable Docker deployment.

## Technology

Python 3.11, FastAPI, Pydantic, SQLite, pytest, Docker, and Postman.
