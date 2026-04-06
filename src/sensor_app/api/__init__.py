"""HTTP **metrics service** built on FastAPI.

Exports :func:`sensor_app.api.main.create_app` for tests and
``sensor_app.api.main:app`` as the default ASGI application.

**Cross-cutting (see** ``main`` **and** ``middleware`` **):**

* **``X-Request-ID``** on every response (optional inbound header for correlation).
* **Rate limits** per route via slowapi, configured through :class:`sensor_app.settings.Settings`.
"""
