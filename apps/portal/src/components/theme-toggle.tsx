import { IconDeviceLaptop, IconMoon, IconSun } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { Button } from "~/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "~/components/ui/dropdown-menu";
import {
  applyTheme,
  readStoredTheme,
  storeTheme,
  watchSystemTheme,
  type Theme,
} from "~/lib/theme";

const OPTIONS: { value: Theme; label: string; icon: typeof IconSun }[] = [
  { value: "light", label: "Light", icon: IconSun },
  { value: "dark", label: "Dark", icon: IconMoon },
  { value: "system", label: "System", icon: IconDeviceLaptop },
];

/**
 * Light, dark, or follow the machine.
 *
 * "System" is an option rather than only a starting point: a reader whose
 * machine turns dark at sunset should not have to come back and switch the
 * portal too.
 */
export function ThemeToggle() {
  // Starts at the default rather than at the stored value: the server has no
  // localStorage, and reading it during render would make the first client
  // render disagree with the HTML. The inline script in `__root` has already
  // put the right class on `<html>` by now; this state only drives the menu.
  const [theme, setTheme] = useState<Theme>("system");

  useEffect(() => setTheme(readStoredTheme()), []);

  useEffect(() => {
    if (theme !== "system") return;
    return watchSystemTheme(() => applyTheme("system"));
  }, [theme]);

  function choose(value: string) {
    const next = value as Theme;
    setTheme(next);
    storeTheme(next);
    applyTheme(next);
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button variant="ghost" size="icon" aria-label="Theme">
            {/* Both icons are rendered and one is hidden by the same class the
                theme sets, so the right one is on screen at first paint —
                choosing in React would show a sun to a dark-mode reader until
                hydration caught up. */}
            <IconSun className="dark:hidden" />
            <IconMoon className="hidden dark:block" />
          </Button>
        }
      />
      <DropdownMenuContent align="end" sideOffset={8} className="w-36">
        <DropdownMenuRadioGroup value={theme} onValueChange={choose}>
          {OPTIONS.map((option) => (
            <DropdownMenuRadioItem key={option.value} value={option.value}>
              <option.icon />
              {option.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
