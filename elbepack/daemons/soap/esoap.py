#!/usr/bin/env python
#
# ELBE - Debian Based Embedded Rootfilesystem Builder
# Copyright (C) 2014  Linutronix GmbH
#
# This file is part of ELBE.
#
# ELBE is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# ELBE is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with ELBE.  If not, see <http://www.gnu.org/licenses/>.

import binascii
import soaplib
import os

from soaplib.service import soapmethod
from soaplib.wsgi_soap import SimpleWSGISoapApp
from soaplibfix import String, Integer, Array
from soaplib.serializers.clazz import ClassSerializer
from soaplib.serializers.primitive import Fault, DateTime

from cherrypy.process.plugins import SimplePlugin

from tempfile import NamedTemporaryFile

from elbepack.projectmanager import (ProjectManager, ProjectManagerError,
        InvalidState)

from elbepack.elbexml import ValidationError
from elbepack.db import ElbeDBError, InvalidLogin

class SoapProject (ClassSerializer):
    class types:
        builddir = String
        name = String
        version = String
        status = String
        edit = DateTime

    def __init__(self, prj):
        self.builddir = prj.builddir
        self.name = prj.name
        self.version = prj.version
        self.status = prj.status
        self.edit = prj.edit

class SoapFile (ClassSerializer):
    class types:
        name = String
        description = String

    def __init__(self, fi):
        self.name = fi.name
        self.description = fi.description

class SoapElbeDBError( Fault ):
    def __init__(self, dberr):
        Fault.__init__(self, faultcode="ElbeDBError", faultstring=str(dberr))

class ESoap (SimpleWSGISoapApp, SimplePlugin):

    def __init__ (self,engine):
        SimpleWSGISoapApp.__init__ (self)
        self.pm = ProjectManager ("/var/cache/elbe")
        SimplePlugin.__init__(self,engine)
        self.subscribe()

    def stop(self):
        self.pm.stop()

    @soapmethod (_returns=Array(String))
    def list_users (self):
        try:
            users = self.pm.db.list_users ()
        except ElbeDBError as e:
            raise SoapElbeDBError(e)

        return [u.name for u in users]

    @soapmethod (_returns=Array(SoapProject))
    def list_projects (self):
        try:
            projects = self.pm.db.list_projects ()
        except ElbeDBError as e:
            raise SoapElbeDBError(e)

        return [SoapProject(p) for p in projects]

    @soapmethod (String, _returns=Array(SoapFile))
    def get_files (self, builddir):
        try:
            files = self.pm.db.get_project_files (builddir)
        except ElbeDBError as e:
            raise SoapElbeDBError(e)
        return [SoapFile(f) for f in files]

    @soapmethod (String, String, Integer, _returns=String)
    def get_file (self, builddir, filename, part):
        size = 1024 * 1024 * 5
        pos = size * part
        file_name = builddir + "/" + filename
        file_stat = os.stat (file_name)

        if (pos >= file_stat.st_size):
            return "EndOfFile"

        with file (file_name) as fp:
            if not fp:
                return "FileNotFound"
            try:
                fp.seek (pos)
                data = fp.read (size)
                return binascii.b2a_base64 (data)
            except:
                return "EndOfFile"

    @soapmethod (String, String, String, _returns=String)
    def build (self, user, passwd, builddir):
        try:
            userid = self.pm.db.validate_login(user, passwd)
        except InvalidLogin as e:
            return str (e)

        try:
            self.pm.open_project (userid, builddir)
        except ValidationError as e:
            return "old XML file is invalid - open project failed"
        except Exception as e:
            return str (e) + " - open project failed"

        try:
            self.pm.build_current_project (userid)
        except Exception as e:
            return str (e) + " - build project failed"

        return "OK"

    @soapmethod (String, String, String, String, _returns=String)
    def set_xml (self, user, passwd, builddir, xml):
        try:
            userid = self.pm.db.validate_login(user, passwd)
        except InvalidLogin as e:
            return str (e)

        try:
            self.pm.open_project (userid, builddir)
        except ValidationError as e:
            return "old XML file is invalid - open project failed"
        except Exception as e:
            return str (e) + " - open project failed"

        with NamedTemporaryFile() as fp:
            fp.write (binascii.a2b_base64 (xml))
            fp.flush ()
            try:
                self.pm.set_current_project_xml (userid, fp.name)
            except ProjectManagerError as e:
                return str (e)
            except InvalidState as e:
                return str (e)
            except ElbeDBError as e:
                return str (e)
            except OSError as e:
                return str (e)
            except ValidationError as e:
                return "Invalid XML file"

        return "OK"

    @soapmethod (String, String, String, _returns=String)
    def reset_project (self, user, passwd, builddir):
        try:
            userid = self.pm.db.validate_login(user, passwd)
        except InvalidLogin as e:
            return str (e)

        try:
            self.pm.db.reset_project (builddir, True)
        except Exception as e:
            return str (e)

        return "OK"

    @soapmethod (String, String, String, _returns=String)
    def del_project (self, user, passwd, builddir):
        try:
            userid = self.pm.db.validate_login(user, passwd)
        except InvalidLogin as e:
            return str (e)

        try:
            self.pm.del_project (userid, builddir)
        except Exception as e:
            return str (e)

        return "OK"

    @soapmethod (String, String, String, _returns=String)
    def create_project (self, user, passwd, xml):
        try:
            userid = self.pm.db.validate_login(user, passwd)
        except InvalidLogin as e:
            return str (e)

        with NamedTemporaryFile() as fp:
            fp.write (binascii.a2b_base64 (xml))
            fp.flush ()
            try:
                self.pm.create_project (userid, fp.name)
            except ProjectManagerError as e:
                return str (e)
            except ElbeDBError as e:
                return str (e)
            except OSError as e:
                return str (e)
            except ValidationError as e:
                return "Invalid XML file"

        return "OK"
