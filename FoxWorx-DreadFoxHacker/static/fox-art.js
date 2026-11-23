/* DreadFox portrait — the "Fox Redesign" kitsune drawn as SVG (stage 1-9 x mood).
   Geometry, palettes and faces follow the design's 200x200 artboards; the desktop widget
   (~/h1/fox_art.py) draws the same fox on a Tk canvas. Only static, built-in shapes and the
   pet's stage/mood/foxfire numbers are used; nothing here reads workbench data. */
(function () {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const INK = '#0E0809', EAR_TIP = '#2B1E22', EAR_SHADE = '#3A2629', CLAW = '#F4EEE6', MOUTH = '#3A0C12';

  // Per stage: glow/foxfire, coat, outline/paws, tail tip, chest+muzzle, eye, cheek mark.
  const STAGE = [
    {glow:'#FF9F2E', fur:'#D9772E', dark:'#4A220E', tip:'#F4E6D2', chest:'#F2E4D2', eye:'#FFB52E', cheek:'#4A220E'},
    {glow:'#FFB13B', fur:'#D2601E', dark:'#45190A', tip:'#F6E4CE', chest:'#F2E2CE', eye:'#FFD23F', cheek:'#45190A'},
    {glow:'#FF5A36', fur:'#C7361F', dark:'#3E0E08', tip:'#FFE2CC', chest:'#F4E0D0', eye:'#FFE14D', cheek:'#3E0E08'},
    {glow:'#6BEB5A', fur:'#9C4A22', dark:'#24140A', tip:'#B6E07A', chest:'#EADBC6', eye:'#9CFF57', cheek:'#24140A'},
    {glow:'#A35CFF', fur:'#8C2F4A', dark:'#220914', tip:'#D9B8FF', chest:'#EBD9E0', eye:'#C77DFF', cheek:'#220914'},
    {glow:'#7FC8FF', fur:'#4E5F8A', dark:'#10162A', tip:'#DDE8FF', chest:'#D9E2F2', eye:'#6FD3FF', cheek:'#10162A'},
    {glow:'#CFE9FF', fur:'#B9C0CC', dark:'#262A33', tip:'#FFFFFF', chest:'#F2F4F8', eye:'#7FE7FF', cheek:'#262A33'},
    {glow:'#FFC93B', fur:'#C98A22', dark:'#2E1C04', tip:'#FFF0B8', chest:'#F4E8CC', eye:'#FF6A3D', cheek:'#2E1C04'},
    {glow:'#FF3B4A', fur:'#F4EEE6', dark:'#1E1416', tip:'#D1142E', chest:'#FFFFFF', eye:'#FF2A2A', cheek:'#D1142E'},
  ];
  // Mood → face. The eight Moods-artboard faces are exact; Sleepy/Inquisitive/Unhatched extend them.
  const MOOD = {
    Content:     {head:0,  ears:6,  eye:'base',   mouth:'smile', snarl:false, extra:null},
    Delighted:   {head:0,  ears:6,  eye:'soft',   mouth:'grin',  snarl:false, extra:'sparkle'},
    Confident:   {head:0,  ears:6,  eye:'soft',   mouth:'smirk', snarl:false, extra:null},
    Determined:  {head:0,  ears:6,  eye:'base',   mouth:'snarl', snarl:true,  extra:null},
    Frustrated:  {head:0,  ears:40, eye:'narrow', mouth:'snarl', snarl:true,  extra:'anger'},
    Wary:        {head:0,  ears:40, eye:'wide',   mouth:'snarl', snarl:true,  extra:null},
    Remembering: {head:5,  ears:6,  eye:'lookup', mouth:'smirk', snarl:false, extra:'sparkle'},
    Dreaming:    {head:-8, ears:6,  eye:'closed', mouth:'smile', snarl:false, extra:'zzz'},
    Sleepy:      {head:-4, ears:18, eye:'closed', mouth:'smile', snarl:false, extra:'z'},
    Inquisitive: {head:8,  ears:6,  eye:'wide',   mouth:'smile', snarl:false, extra:null},
  };
  const EYES = {
    base:   {up:89,   low:104,   rx:1.8, ry:5.5, dx:0,   dy:0,  hl:true},
    soft:   {up:93.5, low:103.5, rx:1.8, ry:3,   dx:0,   dy:0,  hl:true},
    narrow: {up:95.5, low:102,   rx:1.8, ry:1.8, dx:0,   dy:0,  hl:false},
    wide:   {up:87,   low:105,   rx:1.0, ry:6,   dx:0,   dy:0,  hl:true},
    lookup: {up:89,   low:104,   rx:1.8, ry:5,   dx:2.5, dy:-2, hl:true},
  };
  const P = {
    tail:'M -7,0 C -24,-21 -27,-42 -16,-57.4 L -7,-53.2 L -2,-75.6 L 6,-56 L 17,-61.6 L 14,-49 C 24,-38.5 20,-21 7,0 Z',
    shine:'M 1,-8 Q -5,-31.5 1,-49',
    body:'M 70,180 C 64,152 72,130 88,126 L 100,132 L 112,126 C 128,130 136,152 130,180 Z',
    arms:'M 64,160 L 58,150 L 68,152 Z M 136,160 L 142,150 L 132,152 Z',
    chest:'M 86,134 L 93,141 L 100,136 L 107,141 L 114,134 L 114,152 L 108,163 L 104,159 L 100,173 L 96,159 L 92,163 L 86,152 Z',
    paws:'M 78,184 L 78,174 Q 88,168 98,174 L 98,184 Z M 102,184 L 102,174 Q 112,168 122,174 L 122,184 Z',
    claws:'M 80,184 l 2,5 l 2,-5 M 86,184 l 2,5 l 2,-5 M 92,184 l 2,5 l 2,-5 M 104,184 l 2,5 l 2,-5 M 110,184 l 2,5 l 2,-5 M 116,184 l 2,5 l 2,-5',
    ear:'M 60,88 C 52,64 48,40 46,18 C 66,34 84,50 94,70 Z',
    earTip:'M 46,18 C 54,25 62,32 69,38 L 51,50 C 49,40 47,29 46,18 Z',
    earInner:'M 66,80 C 60,62 56,46 54,32 C 66,44 76,54 86,68 Z',
    ruff:'M 100,60 L 124,64 L 142,78 L 150,95 L 168,100 L 154,107 L 164,116 L 146,117 L 150,126 L 132,124 L 100,142 L 68,124 L 50,126 L 54,117 L 36,116 L 46,107 L 32,100 L 50,95 L 58,78 L 76,64 Z',
    muzzle:'M 100,102 L 116,105 L 148,104 L 138,114 L 142,120 L 126,122 L 100,142 L 74,122 L 58,120 L 62,114 L 52,104 L 84,105 Z',
    sparkle:'M 150,58 L 152,64 L 158,66 L 152,68 L 150,74 L 148,68 L 142,66 L 148,64 Z',
  };
  const MIRROR = 'matrix(-1 0 0 1 200 0)';
  let renders = 0;   // gradient/clip ids must be unique when several foxes share a page

  function E(tag, attrs, kids) {
    const el = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) el.setAttribute(k, String(v));
    for (const kid of kids || []) if (kid) el.append(kid);
    return el;
  }
  const path = (d, a) => E('path', Object.assign({d}, a));

  function tailAngles(n) {
    if (n <= 1) return [48];
    const spread = Math.min(75, 18.5 + 6.5 * n);
    const angs = Array.from({length:n}, (_, k) => -spread + k * 2 * spread / (n - 1));
    return angs.sort((a, b) => Math.abs(b) - Math.abs(a) || a - b);    // back to front
  }
  function orbPositions(n) {
    const angs = n <= 1 ? [0] : Array.from({length:n}, (_, k) => -75 + k * 150 / (n - 1));
    return angs.map(a => [100 + 90 * Math.sin(a * Math.PI / 180), 112 - 90 * Math.cos(a * Math.PI / 180)]);
  }

  function forehead(stage, fur) {
    switch (stage) {
      case 2: return [E('circle', {cx:100, cy:76, r:2.6, fill:'#FFE6CC'})];
      case 3: return [path('M 93,72 L 100,79 L 107,72', {fill:'none', stroke:'#FFE2C8', 'stroke-width':2.6, 'stroke-linecap':'round', 'stroke-linejoin':'round'})];
      case 4: return [path('M 100,70 C 106,74 106,80 100,84 C 94,80 94,74 100,70 Z', {fill:'#4E9A5E'}),
                      path('M 100,72 L 100,82', {stroke:'#FFFFFF', 'stroke-opacity':.6, 'stroke-width':1})];
      case 5: return [path('M 100,69 L 105,77 L 100,85 L 95,77 Z', {fill:'none', stroke:'#9B6BF0', 'stroke-width':2.2, 'stroke-linejoin':'round'}),
                      E('circle', {cx:100, cy:77, r:1.8, fill:'#9B6BF0'})];
      // The design file's crescent path is degenerate (draws nothing); this is the intended moon.
      case 6: return [E('circle', {cx:100, cy:77, r:7, fill:'#FFFFFF'}), E('circle', {cx:103.2, cy:75.4, r:6, fill:fur})];
      case 7: return [[94, 78], [100, 73], [106, 78]].map(([x, y]) => E('circle', {cx:x, cy:y, r:2.1, fill:'#7C8BA6'}));
      case 8: return [E('polygon', {points:'100,70 101.8,74.6 106.7,74.8 102.9,77.9 104.1,82.7 100,80 95.9,82.7 97.1,77.9 93.3,74.8 98.2,74.6', fill:'#FFF3C4'})];
      case 9: return [path('M 100,66 C 106,73 106,80 100,86 C 94,80 94,73 100,66 Z', {fill:'#E23A4E'})];
      default: return [];
    }
  }

  function eyes(face, art, fur) {
    const out = [];
    // Shapes drawn for the left eye, then mirrored; pupils glance the same way in both eyes.
    const left = [path('M 72,100 L 64,113 L 76,104 Z', {fill:art.cheek})];
    if (face.eye === 'closed') {
      left.push(path('M 89,100 L 69,92', {stroke:INK, 'stroke-width':2.8, 'stroke-linecap':'round'}));
    } else {
      const e = EYES[face.eye];
      left.push(path(`M 89,100 Q 80,${e.up} 69,92 Q 78,${e.low} 89,100 Z`, {fill:art.eye, class:'fx-eye'}));
      left.push(path(`M 89,100 Q 80,${e.up} 69,92`, {stroke:INK, 'stroke-width':2.8, fill:'none', 'stroke-linecap':'round'}));
    }
    left.push(path('M 69,92 l -4,-2', {stroke:INK, 'stroke-width':2.4, 'stroke-linecap':'round'}));
    left.push(path('M 67,82 L 89,90 L 87,93 L 68,86 Z', {fill:INK, 'fill-opacity':.85}));
    out.push(E('g', {}, left), E('g', {transform:MIRROR}, left.map(n => n.cloneNode(true))));
    if (face.eye !== 'closed') {
      const e = EYES[face.eye];
      for (const [px, hx] of [[80, 82.6], [120, 117.4]]) {
        out.push(E('ellipse', {cx:px + e.dx, cy:96 + e.dy, rx:e.rx, ry:e.ry, fill:INK, class:'fx-pupil'}));
        if (e.hl) out.push(E('circle', {cx:hx + e.dx, cy:93.8 + e.dy, r:1.3, fill:'#FFFFFF', 'fill-opacity':.9}));
      }
    }
    return out;
  }

  function mouth(face) {
    const W = '#FFFFFF', fang = d => path(d, {fill:W, stroke:INK, 'stroke-width':.6});
    const out = [path('M 94,111 L 106,111 L 100,118 Z', {fill:INK, stroke:INK, 'stroke-width':1.5, 'stroke-linejoin':'round'}),
                 path('M 100,118 L 100,121', {stroke:INK, 'stroke-width':1.6})];
    const line = d => path(d, {stroke:INK, 'stroke-width':2, fill:'none', 'stroke-linecap':'round', 'stroke-linejoin':'round'});
    if (face.mouth === 'smile') out.push(line('M 90,123 L 100,125 L 110,123'), fang('M 90.8,123.6 L 95.2,123.6 L 93,128.6 Z'), fang('M 104.8,123.6 L 109.2,123.6 L 107,128.6 Z'));
    if (face.mouth === 'smirk') out.push(line('M 91,123 L 100,125 L 111,120'), fang('M 103.8,122.5 L 108.2,122.5 L 106,128.5 Z'));
    if (face.mouth === 'snarl') out.push(
      path('M 89,121 L 111,121 L 108,130 L 100,133 L 92,130 Z', {fill:MOUTH, stroke:INK, 'stroke-width':1.6, 'stroke-linejoin':'round'}),
      fang('M 89.8,121 L 94.2,121 L 92,128 Z'), fang('M 105.8,121 L 110.2,121 L 108,128 Z'),
      path('M 95,121 L 97,124 L 99,121 L 101,121 L 103,124 L 105,121 Z', {fill:W}),
      path('M 95,132 L 97,128 L 99,132.5 Z M 105,132 L 103,128 L 101,132.5 Z', {fill:W}));
    if (face.mouth === 'grin') out.push(
      path('M 86,122 L 114,122 L 111,129 L 100,132 L 89,129 Z', {fill:MOUTH, stroke:INK, 'stroke-width':1.6, 'stroke-linejoin':'round'}),
      fang('M 86.8,122 L 91.2,122 L 89,129 Z'), fang('M 108.8,122 L 113.2,122 L 111,129 Z'),
      path('M 92,122 L 94,125 L 96,122 L 104,122 L 106,125 L 108,122 Z', {fill:W}),
      path('M 95,131 L 97,127 L 99,131.5 Z M 105,131 L 103,127 L 101,131.5 Z', {fill:W}));
    if (face.snarl) out.push(path('M 92,108 L 96,106 M 108,108 L 104,106 M 94,104 L 97,103 M 106,104 L 103,103', {stroke:INK, 'stroke-width':1.4, 'stroke-linecap':'round'}));
    return out;
  }

  function extras(kind) {
    if (kind === 'sparkle') return [E('g', {fill:'#FFE08A', class:'fx-twinkle'}, [path(P.sparkle), E('circle', {cx:162, cy:52, r:2})])];
    if (kind === 'anger') return [E('g', {stroke:'#FF6B6B', 'stroke-width':2.4, 'stroke-linecap':'round', class:'fx-shake'},
      [path('M 146,66 l 6,-6'), path('M 150,74 l 9,-2'), path('M 140,62 l 1,-8')])];
    if (kind === 'zzz' || kind === 'z') {
      const letters = [[146, 70, 16, 1], [158, 54, 12, .75], [167, 42, 9, .5]].slice(0, kind === 'zzz' ? 3 : 1);
      return [E('g', {fill:'#C9D6F2', 'font-family':'Fredoka, DejaVu Sans, sans-serif', 'font-weight':600, class:'fx-zzz'},
        letters.map(([x, y, size, op]) => { const t = E('text', {x, y, 'font-size':size, 'fill-opacity':op}); t.textContent = 'z'; return t; }))];
    }
    return [];
  }

  /** Replace the contents of ``svg`` with the fox for this pet snapshot. */
  function render(svg, model) {
    const stage = Math.max(1, Math.min(9, Number(model.tails) || 1));
    const art = STAGE[stage - 1];
    const face = MOOD[model.mood] || MOOD.Content;
    const fire = Math.max(0.25, Math.min(1, Number(model.foxfire ?? 0.7)));
    const fur = art.fur;
    const id = 'fx' + (++renders) + '-';
    svg.setAttribute('viewBox', '0 0 200 200');
    const defs = E('defs', {}, [
      E('radialGradient', {id:id + 'a'}, [E('stop', {offset:0, 'stop-color':art.glow, 'stop-opacity':(0.32 * (0.6 + 0.4 * fire)).toFixed(3)}),
                                       E('stop', {offset:1, 'stop-color':art.glow, 'stop-opacity':0})]),
      E('radialGradient', {id:id + 'o'}, [E('stop', {offset:0, 'stop-color':'#FFFFFF'}), E('stop', {offset:.35, 'stop-color':art.glow}),
                                       E('stop', {offset:1, 'stop-color':art.glow, 'stop-opacity':0})]),
      E('clipPath', {id:id + 't'}, [E('rect', {x:-40, y:-84, width:80, height:32.9})]),
    ]);
    const nodes = [defs, E('circle', {cx:100, cy:112, r:96, fill:`url(#${id}a)`})];
    if (stage === 5 || stage === 8) nodes.push(E('circle', {cx:100, cy:108, r:84, fill:'none', stroke:art.glow, 'stroke-opacity':.35, 'stroke-width':1, 'stroke-dasharray':'1 6'}));
    if (stage === 9) nodes.push(E('circle', {cx:100, cy:108, r:86, fill:'none', stroke:'#E8B04A', 'stroke-opacity':.55, 'stroke-width':1.5}),
                                E('circle', {cx:100, cy:108, r:80, fill:'none', stroke:'#E8B04A', 'stroke-opacity':.25, 'stroke-width':1, 'stroke-dasharray':'2 5'}));
    tailAngles(stage).forEach((a, i) => nodes.push(
      E('g', {transform:`translate(100,152) rotate(${a.toFixed(1)}) scale(${(1 - Math.abs(a) / 750).toFixed(3)})`}, [
        E('g', {class:'fx-sway', style:`--i:${i}`}, [
          path(P.tail, {fill:fur, stroke:art.dark, 'stroke-opacity':.35, 'stroke-width':1.2}),
          path(P.tail, {fill:art.tip, 'clip-path':`url(#${id}t)`}),
          path(P.shine, {stroke:'#FFFFFF', 'stroke-opacity':.22, 'stroke-width':3, fill:'none', 'stroke-linecap':'round'})])])));
    orbPositions(stage).forEach(([x, y], i) => nodes.push(E('g', {class:'fx-orb', style:`--i:${i}`, opacity:fire.toFixed(2)}, [
      E('circle', {cx:x.toFixed(1), cy:y.toFixed(1), r:8, fill:`url(#${id}o)`}),
      E('circle', {cx:x.toFixed(1), cy:y.toFixed(1), r:2.4, fill:'#FFFFFF'})])));
    nodes.push(path(P.body, {fill:fur}), path(P.arms, {fill:fur}), path(P.chest, {fill:art.chest}), path(P.paws, {fill:art.dark}),
               path(P.claws, {fill:CLAW, stroke:CLAW, 'stroke-width':.8, 'stroke-linejoin':'round'}));
    const ear = () => [path(P.ear, {fill:fur}), path(P.earTip, {fill:EAR_TIP}),
                       path(P.earInner, {fill:EAR_SHADE, 'fill-opacity':.55}), path(P.earInner, {fill:art.chest, 'fill-opacity':.35})];
    nodes.push(E('g', {transform:`rotate(${face.head},100,100)`}, [
      E('g', {transform:`rotate(${-face.ears},74,76)`}, ear()),
      E('g', {transform:`rotate(${face.ears},126,76)`}, [E('g', {transform:MIRROR}, ear())]),
      path(P.ruff, {fill:fur, stroke:art.dark, 'stroke-opacity':.5, 'stroke-width':1.2, 'stroke-linejoin':'round'}),
      path(P.muzzle, {fill:art.chest}),
      ...forehead(stage, fur),
      ...eyes(face, art, fur), ...mouth(face)]));
    nodes.push(...extras(face.extra));
    svg.replaceChildren(...nodes);
  }

  window.DreadFoxArt = {render, STAGE, MOOD};
})();
