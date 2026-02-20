package wa

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestEventHandlerRegistrationDoesNotCallUnderLock(t *testing.T) {
	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatalf("runtime.Caller failed")
	}
	clientPath := filepath.Join(filepath.Dir(thisFile), "client.go")
	src, err := os.ReadFile(clientPath)
	if err != nil {
		t.Fatalf("read %s: %v", clientPath, err)
	}

	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, clientPath, src, 0)
	if err != nil {
		t.Fatalf("parse %s: %v", clientPath, err)
	}

	check := func(funcName, callName string) {
		t.Helper()

		var fn *ast.FuncDecl
		for _, d := range f.Decls {
			fd, ok := d.(*ast.FuncDecl)
			if !ok || fd.Recv == nil || fd.Name == nil {
				continue
			}
			if fd.Name.Name == funcName {
				fn = fd
				break
			}
		}
		if fn == nil || fn.Body == nil {
			t.Fatalf("could not find function %s", funcName)
		}

		lockDepth := 0
		foundCall := false

		for _, st := range fn.Body.List {
			// Detect "defer c.mu.Unlock()" which is a strong smell here.
			if ds, ok := st.(*ast.DeferStmt); ok {
				if isCallToMuUnlock(ds.Call) {
					t.Fatalf("%s uses defer mu.Unlock(); expected unlock before calling %s", funcName, callName)
				}
			}

			// Track c.mu.Lock()/Unlock() depth at statement granularity.
			ast.Inspect(st, func(n ast.Node) bool {
				ce, ok := n.(*ast.CallExpr)
				if !ok {
					return true
				}
				if isCallToMuLock(ce) {
					lockDepth++
				}
				if isCallToMuUnlock(ce) {
					if lockDepth > 0 {
						lockDepth--
					}
				}
				if isCallToMethod(ce, callName) {
					foundCall = true
					if lockDepth != 0 {
						pos := fset.Position(ce.Pos())
						t.Fatalf("%s calls %s while holding mu (depth=%d) at %s", funcName, callName, lockDepth, pos)
					}
				}
				return true
			})
		}

		if !foundCall {
			t.Fatalf("%s: expected to find a call to %s", funcName, callName)
		}
	}

	check("AddEventHandler", "AddEventHandler")
	check("RemoveEventHandler", "RemoveEventHandler")
}

func isCallToMethod(call *ast.CallExpr, method string) bool {
	if call == nil {
		return false
	}
	sel, ok := call.Fun.(*ast.SelectorExpr)
	return ok && sel.Sel != nil && sel.Sel.Name == method
}

func isCallToMuLock(call *ast.CallExpr) bool {
	return isCallToSelector(call, "mu", "Lock")
}

func isCallToMuUnlock(call *ast.CallExpr) bool {
	return isCallToSelector(call, "mu", "Unlock")
}

func isCallToSelector(call *ast.CallExpr, field, method string) bool {
	if call == nil {
		return false
	}
	sel, ok := call.Fun.(*ast.SelectorExpr)
	if !ok || sel.Sel == nil || sel.Sel.Name != method {
		return false
	}
	xsel, ok := sel.X.(*ast.SelectorExpr)
	if !ok || xsel.Sel == nil || xsel.Sel.Name != field {
		return false
	}
	// We don't care whether it's c.mu or something.mu; the rule is "don't call into whatsmeow while holding mu".
	return true
}
