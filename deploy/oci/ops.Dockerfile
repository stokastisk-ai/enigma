# Operator toolbox for the import/restore on the private network: pg_dump/pg_restore 18.7 (same as the server) + Python with psycopg 3.
FROM postgres:18.7-bookworm@sha256:e742a2668255221388004f3a42b6150a2103f5054ab3a4a93ae9e4f01e551c35
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-psycopg && rm -rf /var/lib/apt/lists/*
WORKDIR /work
USER postgres
