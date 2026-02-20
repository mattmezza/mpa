package pathutil

import (
	"path/filepath"
	"strings"
)

var replacer = strings.NewReplacer(
	"/", "_",
	"\\", "_",
	":", "_",
	"@", "_",
	"?", "_",
	"*", "_",
	"<", "_",
	">", "_",
	"|", "_",
)

func SanitizeSegment(seg string) string {
	seg = strings.TrimSpace(seg)
	if seg == "" {
		return "unknown"
	}
	seg = replacer.Replace(seg)
	seg = strings.ReplaceAll(seg, "..", "_")
	seg = strings.ReplaceAll(seg, string(filepath.Separator), "_")
	return seg
}

func SanitizeFilename(name string) string {
	name = strings.TrimSpace(name)
	if name == "" {
		return "file"
	}
	name = replacer.Replace(name)
	name = strings.ReplaceAll(name, "..", "_")
	name = strings.ReplaceAll(name, string(filepath.Separator), "_")
	return name
}
