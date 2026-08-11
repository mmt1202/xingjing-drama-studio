export interface PermissionGate {
  all(...required: string[]): boolean;
  any(...required: string[]): boolean;
}

export function createPermissionGate(granted: Iterable<string>): PermissionGate {
  const permissions = new Set(granted);
  return {
    all: (...required) => required.length > 0 && required.every((permission) => permissions.has(permission)),
    any: (...required) => required.length > 0 && required.some((permission) => permissions.has(permission)),
  };
}
