# Copyright (c) 2013. Librato, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#     * Redistributions of source code must retain the above copyright #       notice, this list of conditions and the following disclaimer.  #     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of Librato, Inc. nor the names of project contributors
#       may be used to endorse or promote products derived from this software
#       without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL LIBRATO, INC. BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
__version__ = "0.4.15"

# Defaults
HOSTNAME = "metrics-api.librato.com"
BASE_PATH = "/v1/"

import platform
import time
import logging
from six.moves import http_client
from six import string_types
import urllib
import base64
import json
import email.message
from librato import exceptions
from librato.queue import Queue
from librato.metrics import Gauge, Counter
from librato.instruments import Instrument
from librato.dashboards import Dashboard
from librato.annotations import Annotation
from librato.alerts import Alert
import pprint

log = logging.getLogger("librato")

# Alias HTTPSConnection so the tests can mock it out.
HTTPSConnection = http_client.HTTPSConnection

# Alias urlencode, it moved between py2 and py3.
try:
    urlencode = urllib.parse.urlencode  # py3
except AttributeError:
    urlencode = urllib.urlencode        # py2


class LibratoConnection(object):
    """Librato API Connection.
    Usage:
    >>> conn = LibratoConnection(username, api_key)
    >>> conn.list_metrics()
    [...]
    """

    def __init__(self, username, api_key, hostname=HOSTNAME, base_path=BASE_PATH):
        """Create a new connection to Librato Metrics.
        Doesn't actually connect yet or validate until you make a request.

        :param username: The username (email address) of the user to connect as
        :type username: str
        :param api_key: The API Key (token) to use to authenticate
        :type api_key: str
        """
        try:
            self.username = username.encode('ascii')
            self.api_key = api_key.encode('ascii')
        except:
            raise TypeError("Librato only supports ascii for the credentials")

        self.hostname = hostname
        self.base_path = base_path
        # these two attributes ared used to control fake server errors when doing
        # unit testing.
        self.fake_n_errors = 0
        self.backoff_logic = lambda backoff: backoff*2

    def _set_headers(self, headers):
        """ set headers for request """
        if headers is None:
            headers = {}
        headers['Authorization'] = b"Basic " + base64.b64encode(self.username + b":" + self.api_key).strip()

        # http://en.wikipedia.org/wiki/User_agent#Format
        # librato-metrics/1.0.3 (ruby; 1.9.3p385; x86_64-darwin11.4.2) direct-faraday/0.8.4
        ua_chunks = []  # Set user agent
        ua_chunks.append("python-librato/" + __version__)
        p = platform
        system_info = (p.python_version(), p.machine(), p.system(), p.release())
        ua_chunks.append("(python; %s; %s-%s%s)" % system_info)
        headers['User-Agent'] = ' '.join(ua_chunks)
        return headers

    def _make_request(self, conn, path, headers, query_props, method):
        """ Perform the an https request to the server """
        uri = self.base_path + path
        body = None
        if query_props:
            if method == "POST" or method == "DELETE" or method == "PUT":
                body = json.dumps(query_props)
                headers['Content-Type'] = "application/json"
            else:
                uri += "?" + urlencode(query_props)

        log.debug("method=%s uri=%s" % (method, uri))
        log.debug("body(->): %s" % body)
        conn.request(method, uri, body=body, headers=headers)
        return conn.getresponse()

    def _process_response(self, resp, backoff):
        """ Process the response from the server """
        success = True
        resp_data = None
        not_a_server_error = resp.status < 500

        if not_a_server_error:
            body = resp.read()
            if body:
                resp_data = json.loads(body.decode(_getcharset(resp)))
            log.debug("body(<-): %s" % body)
            a_client_error = resp.status >= 400
            if a_client_error:
                raise exceptions.get(resp.status, resp_data)
            return resp_data, success, backoff, resp.status
        else:  # A server error, wait and retry
            backoff = self.backoff_logic(backoff)
            log.debug("%s: waiting %s before re-trying" % (resp.status, backoff))
            time.sleep(backoff)
            return None, not success, backoff, resp.status

    def _mexe(self, path, method="GET", query_props=None, p_headers=None):
        """Internal method for executing a command.
           If we get server errors we exponentially wait before retrying
        """
        conn = self._setup_connection()
        headers = self._set_headers(p_headers)
        success = False
        backoff = 1
        resp_data = None
        while not success:
            resp = self._make_request(conn, path, headers, query_props, method)
            resp_data, success, backoff, status = self._process_response(resp, backoff)
        return resp_data

    def _do_we_want_to_fake_server_errors(self):
        return self.fake_n_errors > 0

    def _setup_connection(self):
        if self._do_we_want_to_fake_server_errors():
            return HTTPSConnection(self.hostname, fake_n_errors=self.fake_n_errors)
        else:
            return HTTPSConnection(self.hostname)

    def _parse(self, resp, name, cls):
        """Parse to an object"""
        if name in resp:
            return [cls.from_dict(self, m) for m in resp[name]]
        else:
            return resp

    #
    # Metrics
    #
    def list_metrics(self, **query_props):
        """List all the metrics available"""
        from librato.metrics import Metric
        resp = self._mexe("metrics", query_props=query_props)
        return self._parse(resp, "metrics", Metric)

    def submit(self, name, value, type="gauge", **query_props):
        payload = {'gauges': [], 'counters': []}
        metric = {'name': name, 'value': value}
        for k, v in query_props.items():
            metric[k] = v
        payload[type + 's'].append(metric)
        self._mexe("metrics", method="POST", query_props=payload)

    def get(self, name, **query_props):
        resp = self._mexe("metrics/%s" % name, method="GET", query_props=query_props)
        if resp['type'] == 'gauge':
            return Gauge.from_dict(self, resp)
        elif resp['type'] == 'counter':
            return Gauge.from_dict(self, resp)
        else:
            raise Exception('The server sent me something that is not a Gauge nor a Counter.')

    def update(self, name, **query_props):
        resp = self._mexe("metrics/%s" % name, method="PUT", query_props=query_props)

    def delete(self, names):
        path = "metrics/%s" % names
        payload = {}
        if not isinstance(names, string_types):
            payload = {'names': names}
            path = "metrics"
        return self._mexe(path, method="DELETE", query_props=payload)

    #
    # Dashboards!
    #
    def list_dashboards(self, **query_props):
        """List all dashboards"""
        resp = self._mexe("dashboards", query_props=query_props)
        return self._parse(resp, "dashboards", Dashboard)

    def get_dashboard(self, id, **query_props):
        """Get specific dashboard by ID"""
        resp = self._mexe("dashboards/%s" % id,
                          method="GET", query_props=query_props)
        return Dashboard.from_dict(self, resp)

    def update_dashboard(self, dashboard, **query_props):
        """Update an existing dashboard"""
        payload = dashboard.get_payload()
        for k, v in query_props.items():
            payload[k] = v
        resp = self._mexe("dashboards/%s" % dashboard.id,
                          method="PUT", query_props=payload)
        return resp

    def create_dashboard(self, name, **query_props):
        payload = Dashboard(self, name).get_payload()
        for k, v in query_props.items():
            payload[k] = v
        resp = self._mexe("dashboards", method="POST", query_props=payload)
        return Dashboard.from_dict(self, resp)

    #
    # Instruments
    #
    def list_instruments(self, **query_props):
        """List all instruments"""
        resp = self._mexe("instruments", query_props=query_props)
        return self._parse(resp, "instruments", Instrument)

    def get_instrument(self, id, **query_props):
        """Get specific instrument by ID"""
        # TODO: Add better handling around 404s
        resp = self._mexe("instruments/%s" % id, method="GET", query_props=query_props)
        return Instrument.from_dict(self, resp)

    def update_instrument(self, instrument, **query_props):
        """Update an existing instrument"""
        payload = instrument.get_payload()
        for k, v in query_props.items():
            payload[k] = v
        resp = self._mexe("instruments/%s" % instrument.id, method="PUT", query_props=payload)
        return resp

    def create_instrument(self, name, **query_props):
        """Create a new instrument"""
        payload = Instrument(self, name).get_payload()
        for k, v in query_props.items():
            payload[k] = v
        resp = self._mexe("instruments", method="POST", query_props=payload)
        return Instrument.from_dict(self, resp)

    #
    # Annotations
    #
    def list_annotation_streams(self, **query_props):
        """List all annotation streams"""
        resp = self._mexe("annotations", query_props=query_props)
        return self._parse(resp, "annotations", Annotation)

    def get_annotation_stream(self, name, **query_props):
        """Get an annotation stream (add start_date to query props for events)"""
        resp = self._mexe("annotations/%s" % name, method="GET", query_props=query_props)
        return Annotation.from_dict(self, resp)

    def get_annotation(self, name, id, **query_props):
        """Get a specific annotation event by ID"""
        resp = self._mexe("annotations/%s/%s" % (name,id), method="GET", query_props=query_props)
        return Annotation.from_dict(self, resp)

    def update_annotation_stream(self, name, **query_props):
        """Update an annotation streams metadata"""
        payload = Annotation(self, name).get_payload()
        for k, v in query_props.items():
            payload[k] = v
        resp = self._mexe("annotations/%s" % name, method="PUT", query_props=payload)
        return Annotation.from_dict(self, resp)

    def post_annotation(self, name, **query_props):
        """Create an annotation event on :name.
		  If the annotation stream does not exist, it will be created automatically."""
        resp = self._mexe("annotations/%s" % name, method="POST", query_props=query_props)
        return resp

    def delete_annotation_stream(self, name, **query_props):
        """delete an annotation stream """
        resp = self._mexe("annotations/%s" % name, method="DELETE", query_props=query_props)
        return resp

    #
    # Alerts
    #
    def list_alerts(self, **query_props):
        """List all alerts"""
        resp = self._mexe("alerts", query_props=query_props)
        return self._parse(resp, "alerts", Alert)

    def get_alert(self, id, **query_props):
        """Get a particular alert"""
        # if isinstance(id, (int, long)) or id.isdigit():
        resp = self._mexe("alerts/{}".format(id), method="GET", query_props=query_props)
        alert = Alert.from_dict(self, resp)
        # else:
        #     query_props['name'] = id
        #     resp = self._mexe("alerts", method="GET", query_props=query_props)
        #     alert = self._parse(resp, "alerts", Alert)[0]
        return alert

    def update_alert(self, alert, **query_props):
        """Update a particular alert"""
        if isinstance(alert, (int, long, str)):
            alert = self.get_alert(alert)
        if alert is None: return False

        payload = alert.get_payload()
        for k, v in query_props.items():
            payload[k] = v
        resp = self._mexe("alerts/{}".format(alert.id), method="PUT", query_props=payload)
        return True

    def create_alert(self, name, **query_props):
        payload = Alert(self, name=name).get_payload()
        payload.pop('id', None)
        payload['active'] = True
        payload['rearm_seconds'] = 600
        for k, v in query_props.items():
            payload[k] = v
        resp = self._mexe("alerts", method="POST", query_props=payload)
        return Alert.from_dict(self, resp)

    def delete_alert(self, alert, **query_props):
        if isinstance(alert, (int, long, str)):
            alert = self.get_alert(alert)
        if alert is None: return False

        # payload = alert.get_payload()
        # for k, v in query_props.items():
        #     payload[k] = v
        self._mexe("alerts/{}".format(alert.id), method="DELETE", query_props=query_props)
        return True

    #
    # Queue
    #
    def new_queue(self, **kwargs):
        return Queue(self, **kwargs)


def connect(username, api_key, hostname=HOSTNAME, base_path=BASE_PATH):
    """
    Connect to Librato Metrics
    """
    return LibratoConnection(username, api_key, hostname, base_path)


def _getcharset(resp, default='utf-8'):
    """
    Extract the charset from an HTTPResponse.
    """
    # In Python 3, HTTPResponse is a subclass of email.message.Message, so we
    # can use get_content_chrset. In Python 2, however, it's not so we have
    # to be "clever".
    if hasattr(resp, 'headers'):
        return resp.headers.get_content_charset(default)
    else:
        m = email.message.Message()
        m['content-type'] = resp.getheader('content-type')
        return m.get_content_charset(default)
