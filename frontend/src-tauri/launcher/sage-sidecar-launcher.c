#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char *argv[]) {
  if (argc != 2 || strcmp(argv[1], "--desktop-host") != 0) {
    return 64;
  }

  char executable[PATH_MAX];
  uint32_t executable_size = sizeof(executable);
  if (_NSGetExecutablePath(executable, &executable_size) != 0) {
    return 70;
  }
  char resolved_executable[PATH_MAX];
  if (realpath(executable, resolved_executable) == NULL) {
    return 70;
  }
  char *separator = strrchr(resolved_executable, '/');
  if (separator == NULL) {
    return 70;
  }
  *separator = '\0';

  char candidate[PATH_MAX];
  int written = snprintf(
      candidate, sizeof(candidate),
      "%s/../Resources/sidecar/sage-api-aarch64-apple-darwin",
      resolved_executable);
  if (written < 0 || (size_t)written >= sizeof(candidate)) {
    return 70;
  }
  char sidecar[PATH_MAX];
  if (realpath(candidate, sidecar) == NULL) {
    return 70;
  }

  char expected_prefix[PATH_MAX];
  written = snprintf(expected_prefix, sizeof(expected_prefix),
                     "%s/../Resources/sidecar/", resolved_executable);
  if (written < 0 || (size_t)written >= sizeof(expected_prefix)) {
    return 70;
  }
  char resolved_prefix[PATH_MAX];
  if (realpath(expected_prefix, resolved_prefix) == NULL) {
    return 70;
  }
  size_t prefix_length = strlen(resolved_prefix);
  if (strncmp(sidecar, resolved_prefix, prefix_length) != 0 ||
      sidecar[prefix_length] != '/') {
    return 70;
  }

  char *const sidecar_argv[] = {sidecar, "--desktop-host", NULL};
  execv(sidecar, sidecar_argv);
  return 70;
}
