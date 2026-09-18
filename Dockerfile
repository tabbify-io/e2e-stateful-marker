# The stateful test app the block-store e2e suite deploys.
#
# No build step and no dependencies: the platform's FC runner turns this image
# into a rootfs, and every byte in it that is not the interpreter is a liability
# when the question under test is whether /dev/vdb came back.
FROM python:3.12-alpine

COPY app.py /app.py

# Declared for readers; the platform routes by the manifest's
# `[[runtime.ports]]`, which is the only port declaration it reads.
EXPOSE 3000

CMD ["python", "-u", "/app.py"]
