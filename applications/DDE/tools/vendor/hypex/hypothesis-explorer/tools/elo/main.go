// elo is a CLI for computing Elo ratings and generating tournament pairings
// in the hypothesis-explorer system.
//
// Usage:
//
//	elo <verb> [flags] [args]
//
// Verbs: recompute, pair, standings
//
// See 'elo --help' for the full command tree.
package main

import "github.com/scion-frontiers/hypex/hypothesis-explorer/tools/elo/cmd"

func main() {
	cmd.Execute()
}
