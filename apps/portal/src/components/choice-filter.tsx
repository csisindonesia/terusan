import {
  ChoiceList,
  FilterChip,
  summarise,
  type ChoiceOption,
} from "~/components/filter-chip";
import { toggle } from "~/lib/multi";

/**
 * A filter chip holding a list of choices, for a page that filters in place.
 *
 * The chip and its list are separate components because some filters search
 * the server as the reader types; the administration pages hold every row
 * already, and for them the pair is always wired the same way.
 */
export function ChoiceFilter({
  label,
  options,
  selected,
  onChange,
  searchPlaceholder,
}: {
  label: string;
  options: ChoiceOption[];
  selected: string[];
  onChange: (next: string[] | undefined) => void;
  searchPlaceholder?: string;
}) {
  const labelFor = (value: string) =>
    options.find((option) => option.value === value)?.label ?? value;
  return (
    <FilterChip
      label={label}
      value={summarise(selected, labelFor)}
      onClear={() => onChange(undefined)}
    >
      <ChoiceList
        options={options}
        selected={selected}
        searchPlaceholder={searchPlaceholder}
        onToggle={(value) => onChange(toggle(selected, value))}
        onClear={() => onChange(undefined)}
      />
    </FilterChip>
  );
}
