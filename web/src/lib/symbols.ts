// Broker symbol prefixes -> the chart symbol of the same market. Exness and
// FundedNext name some markets differently from the Dukascopy tables.
const ALIASES: [string, string][] = [
	['DE30', 'DE40'], // Exness
	['GER30', 'DE40'], // FundedNext from here down
	['FRA40', 'FR40'],
	['NDX100', 'USTEC'],
	['SPX500', 'US500'],
	['USOUSD', 'USOIL'],
	['UKOUSD', 'UKOIL'],
	['EUSTX50', 'STOXX50'],
	['NAS100', 'USTEC'], // The5ers from here down
	['SP500', 'US500'],
	['DAX40', 'DE40'],
	['HSIHKD', 'HK50'],
	['F40EUR', 'FR40'],
	['ESXEUR', 'STOXX50']
];

// The chart symbol a broker position belongs to, keeping any broker suffix.
export function chartSymbol(brokerSymbol: string): string {
	const upper = brokerSymbol.toUpperCase();
	const alias = ALIASES.find(([from]) => upper.startsWith(from));
	return alias ? alias[1] + upper.slice(alias[0].length) : upper;
}
