web: gunicorn redeconfianca.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --timeout 120
release: python manage.py migrate --noinput && python manage.py collectstatic --noinput
