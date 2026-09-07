#!/bin/sh

if [ "$POSTGRES_HOST" ]
then
    echo "Waiting for postgres..."

    while ! nc -z $POSTGRES_HOST ${POSTGRES_PORT:-5432}; do
      sleep 0.1
    done

    echo "PostgreSQL started"
fi

echo "Collecting static files..."
python manage.py collectstatic --noinput

exec "$@"
