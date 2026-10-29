# Releases the sounds of the day (drip.py), on the NAS next to the stats.
FROM python:3.12-alpine
RUN apk add --no-cache curl tzdata
WORKDIR /app
COPY drip.py ./
CMD ["python3", "-u", "drip.py"]
