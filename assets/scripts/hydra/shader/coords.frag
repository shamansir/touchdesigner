// hydra `coords` -- identity coordinate map. NOT from hydra; this is the seed
// of a coordinate-mode chain, the `st` that hydra's shader starts from.
//
// RG carries st, so the TOP must be 32-bit float: downstream coord ops push
// these values well outside 0..1 and a fixed-point format would clip them.
//
// Wire: coords -> <coord ops, in REVERSE of the hydra chain order> -> source
//
// Hand written, not generated -- emit_frags.py only writes <function>.frag for
// entries in hydra-functions.json, so this filename is safe from it.

out vec4 fragColor;

void main() {
   fragColor = TDOutputSwizzle(vec4(vUV.st, 0.0, 1.0));
}
