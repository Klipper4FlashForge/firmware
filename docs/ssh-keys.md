# Logging in with an ssh key

Reforge gives you a root password and `ssh root@<printer-ip>` (see [Your root
password](installing.md#your-root-password)). This page replaces the password
prompt with a key, so `ssh`, `scp` and any script or tool that cannot type a
password can reach the printer on its own.

It is not the usual `ssh-copy-id` job. The printer's root filesystem is
read-only, so root has nowhere to keep a `~/.ssh` folder, and the printer's
ssh server is old enough to need one setting on your side. Both are covered
below. Allow fifteen minutes, and keep one password session open until the
last step.

**What this does not do:** password login stays on. A key adds a way in; it
does not close the other one. [Why the password cannot be switched
off](#why-password-login-stays-on) is at the end. Use a long root password.

---

## What has been tried

A checked box happened on that machine; an empty one has not, yet.

- [x] Creator 5 on the stock 1.9.8 base system, with the Reforge mod
      installed. Its ssh server is Dropbear v2019.78.
- [x] Windows 11, the OpenSSH that ships with it (9.5p2), in PowerShell
- [x] Windows 11, the OpenSSH inside Git for Windows (9.1p1), in Git Bash
- [x] An RSA key with no passphrase, using `+ssh-rsa` (step 3)
- [x] The key still logs in after a reboot
- [ ] Creator 5 Pro. Nothing here is specific to the Creator 5, but nobody
      has tried it. Step 1 shows whether your printer matches.
- [ ] macOS, and Linux. The commands are plain OpenSSH and should be the
      same; they have not been run there.
- [ ] A key with a passphrase, loaded through an ssh agent
- [ ] An ECDSA key. The printer's ssh server contains the code for it, but
      nobody has logged in with one. Ed25519 does **not** work: this ssh
      server is older than Ed25519 support.
- [ ] The USB-stick recovery in [If you lock yourself
      out](#if-you-lock-yourself-out)

---

## Before you start

- `ssh root@<printer-ip>` works with your password today.
- You know the printer's IP address. Giving it a fixed address in your router
  keeps the config below valid.
- Your computer has OpenSSH. Windows 10 and 11, macOS and most Linux
  distributions include it; `ssh -V` tells you.

## Step 1: check the printer matches

In your password session on the printer:

```sh
dropbear -V; grep '^root:' /etc/passwd; mount | grep -E ' / | /etc '
```

You should see:

```text
Dropbear v2019.78
root:x:0:0:root:/root:/bin/sh
/dev/root on / type squashfs (ro,relatime)
/dev/mmcblk0p6 on /etc type ext4 (rw,sync,relatime)
```

The parts that matter are `squashfs (ro`, the `root:...:/root:` line and
`/etc` being its own `ext4` mount. If yours differs, stop: the steps below
were written against that output and nothing else.

## Step 2: make a key

On your computer, make a key used for nothing but this printer. It is RSA
because the printer's ssh server cannot read Ed25519 keys.

**Windows (PowerShell):**

```powershell
ssh-keygen -t rsa -b 4096 -C creator5 -f "$HOME\.ssh\id_creator5"
```

**macOS and Linux:**

```sh
ssh-keygen -t rsa -b 4096 -C creator5 -f ~/.ssh/id_creator5
```

It asks for a passphrase. The setup on this page was tried without one. A key
with no passphrase means anyone who copies the file gets root on the printer;
a key with one has to be loaded into an ssh agent before a script can use it,
and that route has not been tried here.

Print the public half, which is the file ending in `.pub`:

```powershell
Get-Content "$HOME\.ssh\id_creator5.pub"
```

```sh
cat ~/.ssh/id_creator5.pub
```

It is one long line starting `ssh-rsa`. Copy the whole line. The file without
`.pub` is the private half and never leaves your computer.

## Step 3: tell your ssh client about the printer

Add this to your ssh config. On Windows that is `C:\Users\<you>\.ssh\config`,
with no file extension (an editor that saves `config.txt` is the usual
trap); on macOS and Linux it is `~/.ssh/config`.

```text
Host creator5 192.168.1.50
  HostName 192.168.1.50
  User root
  IdentityFile ~/.ssh/id_creator5
  IdentitiesOnly yes
  PubkeyAcceptedAlgorithms +ssh-rsa
```

Put your printer's address in both places. The address on the `Host` line is
what makes `ssh root@192.168.1.50` use the key as well as `ssh creator5`:
ssh matches `Host` against what you type, not against the address it ends up
connecting to.

`PubkeyAcceptedAlgorithms +ssh-rsa` is the printer-specific line. The
printer's ssh server can only sign RSA logins the old SHA-1 way, and OpenSSH
8.8 and later refuse that unless told otherwise. Without the line, the login
fails with `Permission denied (publickey,password).` and `ssh -v` ends with
`send_pubkey_test: no mutual signature algorithm`. Before OpenSSH 8.5 the
option is called `PubkeyAcceptedKeyTypes`; current versions accept that name
too, so it is the safer spelling if you are unsure which you have.

Now check what ssh will actually use. A file that is unsaved, or is not the
one ssh reads, looks exactly like a correct one when you open it.

```powershell
ssh -G creator5 | Select-String 'identitiesonly|identityfile|pubkeyaccepted'
```

```sh
ssh -G creator5 | grep -Ei 'identitiesonly|identityfile|pubkeyaccepted'
```

Expect `identitiesonly yes`, your key's path, and `ssh-rsa` somewhere in the
`pubkeyacceptedalgorithms` list. If you see `identitiesonly no`, the config
you edited is not the config being read.

## Step 4: prepare a home for root on the printer

Root's home is `/root`, which sits on the read-only filesystem, so a key
cannot be stored there. The fix is to give root a new home on a writable
disk.

Use `/usr/prog/root`, not `/usr/data/root`. `/usr/data` is a separate
partition, and the boot script reformats a partition it cannot mount. If
root's home is missing, the printer's ssh server refuses every login for
root, passwords included (it reports `Error changing directory`).
`/usr/prog` is the partition the printer's `/etc` comes from: if the printer
can read its passwd file, the new home is there too. Nothing in Reforge's
installer script touches the directory.

In your password session on the printer:

```sh
mkdir -p /usr/prog/root/.ssh
chmod 700 /usr/prog/root /usr/prog/root/.ssh
cp -p /usr/prog/etc/passwd /usr/prog/etc/passwd.before-ssh-key
```

The last line is your way back; it is used twice below. Now add the public
key you copied in step 2, pasted between the quotes:

```sh
echo 'ssh-rsa AAAA...your-whole-line... creator5' >> /usr/prog/root/.ssh/authorized_keys
chmod 600 /usr/prog/root/.ssh/authorized_keys
cat -A /usr/prog/root/.ssh/authorized_keys
```

The last command should print one line that starts `ssh-rsa` and ends in `$`.
A `^M` before the `$` means a carriage return came along; delete the file and
paste again. Keep the modes shown above: they are what the working setup
used. `ssh-copy-id` does not help here, because root's home is read-only
until the next step.

## Step 5: switch root's home, with a safety net

Open a second terminal on your computer now and have the test from step 6
typed out in it. Then, on the printer, arm the safety net: in three minutes it
puts the old passwd file back, so a mistake cannot lock you out.

```sh
nohup sh -c 'sleep 180; cp /usr/prog/etc/passwd.before-ssh-key /usr/prog/etc/passwd; sync' </dev/null >/dev/null 2>&1 &
echo $! > /tmp/passwd-revert.pid
```

Then change root's home:

```sh
sed 's#^root:x:0:0:root:/root:#root:x:0:0:root:/usr/prog/root:#' /usr/prog/etc/passwd > /usr/prog/etc/passwd.new && mv /usr/prog/etc/passwd.new /usr/prog/etc/passwd
chmod 644 /usr/prog/etc/passwd
sync
grep '^root:' /etc/passwd
```

You should see `root:x:0:0:root:/usr/prog/root:/bin/sh`. If it still says
`/root`, the `sed` did not match and nothing changed; cancel the safety net
(next step) and stop.

The `chmod 644` is not optional. Other programs on the printer run as other
users and read this file, and a passwd file only root can read breaks them.
A shell that ssh starts for a single command uses a stricter default than a
login shell, so a file written that way comes out as `600`.

## Step 6: test, then cancel the safety net

In the second terminal, within the three minutes. Single quotes keep your own
shell from filling in `$HOME`:

```powershell
ssh -o BatchMode=yes -o PasswordAuthentication=no creator5 'echo key-login-ok; echo HOME=$HOME'
```

```sh
ssh -o BatchMode=yes -o PasswordAuthentication=no creator5 'echo key-login-ok; echo HOME=$HOME'
```

(In `cmd.exe`, use double quotes.) The answer should be:

```text
key-login-ok
HOME=/usr/prog/root
```

`BatchMode` forbids any prompt, so an answer means the key worked; a password
cannot have let you in. Check the address form too:
`ssh -o BatchMode=yes root@<printer-ip> true` should print nothing and exit 0.

If it works, cancel the safety net on the printer. Leaving it armed would
undo the change:

```sh
kill $(cat /tmp/passwd-revert.pid); rm /tmp/passwd-revert.pid
```

If it fails, do nothing for three minutes. The printer puts the old passwd
file back by itself and your password login is as it was. Then read
[When it does not work](#when-it-does-not-work).

## Step 7: reboot and test again

Power-cycle the printer, wait for it to come up, and run the step 6 command
once more. This is the check that matters: the new home and the passwd change
both live on the printer's persistent disk, and a reboot is the only way to
see that they stayed.

---

## Using it

`ssh creator5` and `ssh root@<printer-ip>` log in without a prompt.

Copying files needs `-O`:

```sh
scp -O creator5:/etc/hostname .
```

The printer has no sftp server, and OpenSSH's `scp` has used sftp since 9.0.
Without `-O` it fails with `sh: /usr/libexec/sftp-server: not found`
followed by `Connection closed`. Anything else that needs an sftp server
fails the same way. `rsync` is not installed on the printer.

## When it does not work

| You see | Why, and what to do |
|---|---|
| `Permission denied (publickey,password).` and `ssh -v` ends `no mutual signature algorithm` | ssh is not applying `+ssh-rsa`. Run the check at the end of step 3. If the key is Ed25519, make an RSA one: the printer cannot read Ed25519. |
| The same message, but `ssh -v` shows the key was offered and refused | The printer is not finding the key. In a password session, run `grep '^root:' /etc/passwd` (it must say `/usr/prog/root`) and `ls -ld /usr/prog/root /usr/prog/root/.ssh /usr/prog/root/.ssh/authorized_keys` (modes `drwx------`, `drwx------`, `-rw-------`, owned by root). If the passwd line says `/root`, the safety net fired or step 5 was skipped. |
| `ssh creator5` logs in, `ssh root@<printer-ip>` asks for a password | The address is not on the `Host` line in step 3. |
| ssh complains about an unknown config option | Your OpenSSH is older than 8.5. Use `PubkeyAcceptedKeyTypes` instead of `PubkeyAcceptedAlgorithms`. |
| Passwords stop working as well | The home directory in the passwd line does not exist. If the safety net is still armed, wait for it. Otherwise see below. |

## If you lock yourself out

Restore the passwd file from the printer's own console or from a USB stick.
The stick route uses the printer's startup script, `/usr/prog/app_startup.sh`,
which runs a file called `runFirmwareExe.sh` from the root of a USB stick as
root at every boot. The copy on the author's printer is identical to the
stock 1.9.8 one and has that hook, but this recovery has not been run.

Format a stick as FAT32 and put one file on it, named exactly
`runFirmwareExe.sh`, saved with Unix line endings (a Windows `CRLF` breaks
the first line):

```sh
#!/bin/sh
cp /usr/prog/etc/passwd.before-ssh-key /usr/prog/etc/passwd
sync
exit 1
```

Plug it in and power-cycle. The `exit 1` matters: on `exit 0` the startup
script treats the file as a firmware install, stops, and sleeps, so the
printer never finishes booting. Remove the stick once the printer is up.

## Going back

From a password session, or from the key login if it still works:

```sh
cp /usr/prog/etc/passwd.before-ssh-key /usr/prog/etc/passwd
chmod 644 /usr/prog/etc/passwd
```

That puts root's home back at `/root`, which always exists. The folder
`/usr/prog/root` can stay or be deleted.

## Why password login stays on

Dropbear can refuse passwords with its `-s` flag. Its flags come from
`/etc/default/dropbear`, which the ssh service's own start script
(`/etc/init.d/S50dropbear`) reads when the server starts. At that moment
`/etc` is still the read-only filesystem's own; the printer's persistent
`/etc` is mounted over it later, by `mount /usr/prog/etc /etc` in
`/usr/prog/app_startup.sh`. A file you put in the persistent `/etc` is not
there yet when the server starts.

That is from reading the startup scripts and has not been tried. Making it
work means changing how the printer boots, which this page does not do. The
root password is the real lock: make it long, and keep the printer off the
internet.
