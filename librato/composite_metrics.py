# Copyright (c) 2013. Librato, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
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

class CompositeMetric(object):
    def __init__(self, connection, compose, resolution, start_time, end_time=None):
        self.connection = connection
        self.measurements = {}
        self.query = {}
        self.compose = compose
        self.resolution = resolution
        self.start_time = start_time
        self.end_time = end_time

    def get_composite(self):
        return self.connection.get_composite(
                self.compose,
                resolution=self.resolution,
                start_time=self.start_time)

    def load(self):
        data = self.get_composite()
        self.measurements = data['measurements']
        self.query = data.get('query', {})
        return data

    # Override
    def sources(self):
        return [m['source']['name'] for m in self.measurements if m['source']]

    def series(self):
        return [m['series'] for m in self.measurements][0]

    def measure_times(self):
        return map(lambda m: m['measure_time'], self.series())

    def values(self):
        return map(lambda m: m['value'], self.series())
