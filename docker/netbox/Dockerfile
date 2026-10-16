ARG NETBOX_IMAGE=netboxcommunity/netbox:v4.7-5.1.1
FROM ${NETBOX_IMAGE}

COPY . /opt/netbox-custom-objects
COPY docker/netbox/plugins.py /etc/netbox/config/plugins.py

RUN /usr/local/bin/uv pip install --python /opt/netbox/venv/bin/python -e /opt/netbox-custom-objects