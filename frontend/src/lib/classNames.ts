/** 참인 클래스 이름만 공백으로 잇는다. */
export function cx(...names: Array<string | false | null | undefined>): string {
  return names.filter((name): name is string => Boolean(name)).join(" ");
}
