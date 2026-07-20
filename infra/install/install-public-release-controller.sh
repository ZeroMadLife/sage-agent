#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "must run as root" >&2
  exit 2
fi

repo_root=${1:-/opt/sage/app}
source_script="$repo_root/scripts/public_releasectl.py"
source_sudoers="$repo_root/infra/sudoers/sage-public-release"

test -f "$source_script"
test -f "$source_sudoers"
python3 -m py_compile "$source_script"
visudo -cf "$source_sudoers"

install -d -o root -g root -m 0700 /var/lib/sage-public-release
install -o root -g root -m 0755 "$source_script" /usr/local/sbin/sage-public-releasectl
install -o root -g root -m 0440 "$source_sudoers" /etc/sudoers.d/sage-public-release
visudo -cf /etc/sudoers.d/sage-public-release

printf '%s\n' '{"action":"status"}' | sudo -u sage-deploy sudo -n /usr/local/sbin/sage-public-releasectl >/dev/null
echo "public release controller installed"
