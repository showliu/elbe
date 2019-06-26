# ELBE - Debian Based Embedded Rootfilesystem Builder
# Copyright (c) 2015-2017 Manuel Traut <manut@linutronix.de>
# Copyright (c) 2016-2017 Torben Hohn <torben.hohn@linutronix.de>
# Copyright (c) 2017 Philipp Arras <philipp.arras@linutronix.de>
# Copyright (c) 2018 Martin Kaistra <martin.kaistra@linutronix.de>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import time
import shutil
import subprocess
import io
import stat
import logging

from elbepack.filesystem import Filesystem
from elbepack.version import elbe_version
from elbepack.hdimg import do_hdimg
from elbepack.fstab import fstabentry
from elbepack.licencexml import copyright_xml
from elbepack.packers import default_packer
from elbepack.shellhelper import CommandError, do, chroot, get_command_out


def copy_filelist(src, filelist, dst):
    for f in filelist:
        f = f.rstrip("\n")
        if src.isdir(f) and not src.islink(f):
            if not dst.isdir(f):
                dst.mkdir(f)
            st = src.stat(f)
            dst.chown(f, st.st_uid, st.st_gid)
        else:
            subprocess.call(["cp", "-a", "--reflink=auto",
                             src.fname(f), dst.fname(f)])
    # update utime which will change after a file has been copied into
    # the directory
    for f in filelist:
        f = f.rstrip("\n")
        if src.isdir(f) and not src.islink(f):
            shutil.copystat(src.fname(f), dst.fname(f))


def extract_target(src, xml, dst, cache):

    # pylint: disable=too-many-locals
    # pylint: disable=too-many-branches

    # create filelists describing the content of the target rfs
    if xml.tgt.has("tighten") or xml.tgt.has("diet"):
        pkglist = [n.et.text for n in xml.node(
            'target/pkg-list') if n.tag == 'pkg']
        arch = xml.text("project/buildimage/arch", key="arch")

        if xml.tgt.has("diet"):
            withdeps = []
            for p in pkglist:
                deps = cache.get_dependencies(p)
                withdeps += [d.name for d in deps]
                withdeps += [p]

            pkglist = list(set(withdeps))

        file_list = []
        for line in pkglist:
            file_list += src.cat_file("var/lib/dpkg/info/%s.list" % (line))
            file_list += src.cat_file("var/lib/dpkg/info/%s.conffiles" %
                                      (line))

            file_list += src.cat_file("var/lib/dpkg/info/%s:%s.list" %
                                      (line, arch))
            file_list += src.cat_file(
                "var/lib/dpkg/info/%s:%s.conffiles" %
                (line, arch))

        file_list = list(sorted(set(file_list)))
        copy_filelist(src, file_list, dst)
    else:
        # first copy most diretories
        for f in src.listdir():
            subprocess.call(["cp", "-a", "--reflink=auto", f, dst.fname('')])

    try:
        dst.mkdir_p("dev")
    except BaseException:
        pass
    try:
        dst.mkdir_p("proc")
    except BaseException:
        pass
    try:
        dst.mkdir_p("sys")
    except BaseException:
        pass

    if xml.tgt.has("setsel"):
        pkglist = [n.et.text for n in xml.node(
            'target/pkg-list') if n.tag == 'pkg']
        psel = 'var/cache/elbe/pkg-selections'

        with open(dst.fname(psel), 'w+') as f:
            for item in pkglist:
                f.write("%s  install\n" % item)

        host_arch = get_command_out("dpkg --print-architecture").strip()
        if xml.is_cross(host_arch):
            ui = "/usr/share/elbe/qemu-elbe/" + str(xml.defs["userinterpr"])
            if not os.path.exists(ui):
                ui = "/usr/bin/" + str(xml.defs["userinterpr"])
            do('cp %s %s' % (ui, dst.fname("usr/bin")))

        chroot(dst.path, "/usr/bin/dpkg --clear-selections")
        chroot(
            dst.path,
            "/usr/bin/dpkg --set-selections < %s " %
            dst.fname(psel))
        chroot(dst.path, "/usr/bin/dpkg --purge -a")


class ElbeFilesystem(Filesystem):
    def __init__(self, path, clean=False):
        Filesystem.__init__(self, path, clean)

    def dump_elbeversion(self, xml):
        f = self.open("etc/elbe_version", "w+")
        f.write("%s %s\n" % (xml.prj.text("name"), xml.prj.text("version")))
        f.write("this RFS was generated by elbe %s\n" % (elbe_version))
        f.write(time.strftime("%c\n"))
        f.close()

        version_file = self.open("etc/updated_version", "w")
        version_file.write(xml.text("/project/version"))
        version_file.close()

        elbe_base = self.open("etc/elbe_base.xml", "wb")
        xml.xml.write(elbe_base)
        self.chmod("etc/elbe_base.xml", stat.S_IREAD)

    def write_licenses(self, f, xml_fname=None):
        licence_xml = copyright_xml()
        for d in self.listdir("usr/share/doc/", skiplinks=True):
            try:
                with io.open(os.path.join(d, "copyright"), "rb") as lic:
                    lic_text = lic.read()
            except IOError as e:
                logging.error("Error while processing license file %s: '%s'" %
                              (os.path.join(d, "copyright"), e.strerror))
                lic_text = "Error while processing license file %s: '%s'" % (
                    os.path.join(d, "copyright"), e.strerror)

            try:
                lic_text = unicode(lic_text, encoding='utf-8')
            except BaseException:
                lic_text = unicode(lic_text, encoding='iso-8859-1')

            if f is not None:
                f.write(unicode(os.path.basename(d)))
                f.write(u":\n======================================"
                        "==========================================")
                f.write(u"\n")
                f.write(lic_text)
                f.write(u"\n\n")

            if xml_fname is not None:
                licence_xml.add_copyright_file(os.path.basename(d), lic_text)

        if xml_fname is not None:
            licence_xml.write(xml_fname)


class ChRootFilesystem(ElbeFilesystem):
    def __init__(self, path, interpreter=None, clean=False):
        ElbeFilesystem.__init__(self, path, clean)
        self.interpreter = interpreter
        self.cwd = os.open("/", os.O_RDONLY)
        self.inchroot = False

    def __del__(self):
        os.close(self.cwd)

    def __enter__(self):
        if self.interpreter:
            if not self.exists("usr/bin"):
                self.mkdir("usr/bin")

            ui = "/usr/share/elbe/qemu-elbe/" + self.interpreter
            if not os.path.exists(ui):
                ui = "/usr/bin/" + self.interpreter
            os.system('cp %s %s' % (ui, self.fname("usr/bin")))

        if self.exists("/etc/resolv.conf"):
            os.system('mv %s %s' % (self.fname("etc/resolv.conf"),
                                    self.fname("etc/resolv.conf.orig")))
        os.system('cp %s %s' % ("/etc/resolv.conf",
                                self.fname("etc/resolv.conf")))

        if self.exists("/etc/apt/apt.conf"):
            os.system('cp %s %s' % (self.fname("/etc/apt/apt.conf"),
                                    self.fname("/etc/apt/apt.conf.orig")))
        if os.path.exists("/etc/apt/apt.conf"):
            os.system('cp %s %s' % ("/etc/apt/apt.conf",
                                    self.fname("/etc/apt/")))

        self.mkdir_p("usr/sbin")
        self.write_file("usr/sbin/policy-rc.d",
                        0o755, "#!/bin/sh\nexit 101\n")

        self.mount()
        return self

    def __exit__(self, _typ, _value, _traceback):
        if self.inchroot:
            self.leave_chroot()
        self.umount()
        if self.interpreter:
            os.system('rm -f %s' %
                      os.path.join(self.path, "usr/bin/" + self.interpreter))

        os.system('rm -f %s' % (self.fname("etc/resolv.conf")))

        if self.exists("/etc/resolv.conf.orig"):
            os.system('mv %s %s' % (self.fname("etc/resolv.conf.orig"),
                                    self.fname("etc/resolv.conf")))

        if self.exists("/etc/apt/apt.conf"):
            os.system('rm -f %s' % (self.fname("etc/apt/apt.conf")))

        if self.exists("/etc/apt/apt.conf.orig"):
            os.system('mv %s %s' % (self.fname("etc/apt/apt.conf.orig"),
                                    self.fname("etc/apt/apt.conf")))

        if self.exists("/usr/sbin/policy-rc.d"):
            os.system('rm -f %s' % (self.fname("usr/sbin/policy-rc.d")))

    def mount(self):
        if self.path == '/':
            return
        try:
            os.system("mount -t proc none %s/proc" % self.path)
            os.system("mount -t sysfs none %s/sys" % self.path)
            os.system("mount -o bind /dev %s/dev" % self.path)
            os.system("mount -o bind /dev/pts %s/dev/pts" % self.path)
        except BaseException:
            self.umount()
            raise

    def enter_chroot(self):
        assert not self.inchroot

        os.environ["LANG"] = "C"
        os.environ["LANGUAGE"] = "C"
        os.environ["LC_ALL"] = "C"

        os.chdir(self.path)
        self.inchroot = True

        if self.path == '/':
            return

        os.chroot(self.path)

    def _umount(self, path):
        if os.path.ismount(path):
            os.system("umount %s" % path)

    def umount(self):
        if self.path == '/':
            return
        self._umount("%s/proc/sys/fs/binfmt_misc" % self.path)
        self._umount("%s/proc" % self.path)
        self._umount("%s/sys" % self.path)
        self._umount("%s/dev/pts" % self.path)
        self._umount("%s/dev" % self.path)

    def leave_chroot(self):
        assert self.inchroot

        os.fchdir(self.cwd)

        self.inchroot = False
        if self.path == '/':
            return

        os.chroot(".")


class TargetFs(ChRootFilesystem):
    def __init__(self, path, xml, clean=True):
        ChRootFilesystem.__init__(self, path, xml.defs["userinterpr"], clean)
        self.xml = xml
        self.images = []
        self.image_packers = {}

    def write_fstab(self, xml):
        if not self.exists("etc"):
            self.mkdir("etc")

        f = self.open("etc/fstab", "w")
        if xml.tgt.has("fstab"):
            for fs in xml.tgt.node("fstab"):
                if not fs.has("nofstab"):
                    fstab = fstabentry(xml, fs)
                    f.write(fstab.get_str())
            f.close()

    def part_target(self, targetdir, grub_version, grub_fw_type=None):

        # create target images and copy the rfs into them
        self.images = do_hdimg(self.xml,
                               targetdir,
                               self,
                               grub_version,
                               grub_fw_type)

        for i in self.images:
            self.image_packers[i] = default_packer

        if self.xml.has("target/package/tar"):
            targz_name = self.xml.text("target/package/tar/name")
            try:
                options = ''
                if self.xml.has("target/package/tar/options"):
                    options = self.xml.text("target/package/tar/options")
                cmd = "tar cfz %(dest)s/%(fname)s -C %(sdir)s %(options)s ."
                args = dict(
                    options=options,
                    dest=targetdir,
                    fname=targz_name,
                    sdir=self.fname('')
                )
                do(cmd % args)
                # only append filename if creating tarball was successful
                self.images.append(targz_name)
            except CommandError:
                # error was logged; continue creating cpio image
                pass

        if self.xml.has("target/package/cpio"):
            oldwd = os.getcwd()
            cpio_name = self.xml.text("target/package/cpio/name")
            os.chdir(self.fname(''))
            try:
                do(
                    "find . -print | cpio -ov -H newc >%s" %
                    os.path.join(
                        targetdir, cpio_name))
                # only append filename if creating cpio was successful
                self.images.append(cpio_name)
            except CommandError:
                # error was logged; continue
                pass

        if self.xml.has("target/package/squashfs"):
            oldwd = os.getcwd()
            sfs_name = self.xml.text("target/package/squashfs/name")
            os.chdir(self.fname(''))
            try:
                do(
                    "mksquashfs %s %s/%s -noappend -no-progress" %
                    (self.fname(''), targetdir, sfs_name))
                # only append filename if creating mksquashfs was successful
                self.images.append(sfs_name)
            except CommandError as e:
                # error was logged; continue
                pass

    def pack_images(self, builddir):
        for img, packer in self.image_packers.items():
            self.images.remove(img)
            packed = packer.pack_file(builddir, img)
            if packed:
                self.images.append(packed)


class BuildImgFs(ChRootFilesystem):
    def __init__(self, path, interpreter):
        ChRootFilesystem.__init__(self, path, interpreter)
