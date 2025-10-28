<script>
   import { onMount, onDestroy } from "svelte";
   import mqtt from "mqtt";

   let mainSynth = null;
   let droneDevice = null;
   let audioContext = null;
   let magentaModel = null;
   let mqttClient = null;

   function setupMQTT() {
      mqttClient = mqtt.connect("ws://localhost:9001");

      mqttClient.on("connect", () => {
         console.log("MQTT Connected");
         mqttClient.subscribe("switch/trigger");
         mqttClient.subscribe("switch/regenerate");
      });

      mqttClient.on("message", (topic, message) => {
         const msg = message.toString();
         console.log(`${topic}: ${msg}`);

         if (topic === "switch/trigger") {
            const [row, col] = msg.split(",").map(Number);
            if (window.p5Instance) {
               window.p5Instance.triggerFromMQTT(row, col);
            }
         }

         if (topic === "switch/regenerate") {
            regenerateMelody();
         }
      });

      mqttClient.on("error", (err) => {
         console.error("MQTT Error:", err);
      });
   }

   function publishState(row, col, state, note) {
      if (mqttClient?.connected) {
         mqttClient.publish(
            "switch/state",
            JSON.stringify({
               row,
               col,
               state,
               note,
               timestamp: Date.now(),
            }),
         );
      }
   }

   async function setupRNBO() {
      const WAContext = window.AudioContext || window.webkitAudioContext;
      audioContext = new WAContext();

      const mainGain = audioContext.createGain();
      const droneGain = audioContext.createGain();
      const masterGain = audioContext.createGain();

      mainGain.gain.value = 0.7;
      droneGain.gain.value = 0.3;

      mainGain.connect(masterGain);
      droneGain.connect(masterGain);
      masterGain.connect(audioContext.destination);

      try {
         const synthResponse = await fetch("/export/synth.export.json");
         const droneResponse = await fetch("/export/drone.export.json");

         if (!synthResponse.ok)
            throw new Error(`Synth load failed: ${synthResponse.status}`);
         if (!droneResponse.ok)
            throw new Error(`Drone load failed: ${droneResponse.status}`);

         const synthPatcher = await synthResponse.json();
         const dronePatcher = await droneResponse.json();

         if (!window.RNBO) {
            await loadRNBOScript(synthPatcher.desc.meta.rnboversion);
         }

         mainSynth = await window.RNBO.createDevice({
            context: audioContext,
            patcher: synthPatcher,
         });

         droneDevice = await window.RNBO.createDevice({
            context: audioContext,
            patcher: dronePatcher,
         });

         mainSynth.node.connect(mainGain);
         droneDevice.node.connect(droneGain);

         console.log("RNBO devices loaded");

         setupMainSynth();
         setupDrone();
      } catch (err) {
         console.error("RNBO setup failed:", err);
      }
   }

   function setupDrone() {
      if (!droneDevice) return;

      try {
         const droneOnParam = droneDevice.parametersById.get("droneOn");
         if (droneOnParam) {
            droneOnParam.value = 1;
            console.log("droneOn = 1");
         }

         const midiPort = 0;
         const note = 48;
         const velocity = 100;
         const currentTime = droneDevice.context.currentTime * 1000;

         const noteOn = [144, note, velocity];
         droneDevice.scheduleEvent(
            new window.RNBO.MIDIEvent(currentTime, midiPort, noteOn),
         );

         console.log("Drone started");
      } catch (err) {
         console.error("Drone setup error:", err);
      }
   }

   function loadRNBOScript(version) {
      return new Promise((resolve, reject) => {
         const el = document.createElement("script");
         el.src = "/lib/rnbo.min.js";
         el.onload = resolve;
         console.log(el.src);
         el.onerror = () => reject(new Error("Failed to load rnbo.js"));
         document.body.append(el);
      });
   }

   function setupMainSynth() {
      if (!mainSynth) return;

      const params = {
         filterCut: 1000,
         filterQ: 0.1,
         reverbTime: 3,
         reverbMix: 0.5,
         filterType: 2,
         "poly/envelope/attack": 200,
         "poly/envelope/decay": 200,
         "poly/envelope/sustain": 0,
         "poly/envelope/release": 1000,
         "poly/oscillator/mode": 1,
         "poly/delay/fb": 0.75,
      };

      Object.entries(params).forEach(([key, value]) => {
         const param = mainSynth.parametersById.get(key);
         if (param) {
            param.value = value;
            console.log(`Main synth: ${key} = ${value}`);
         }
      });
   }

   function playMIDINote(note, duration = 250) {
      if (!mainSynth || !audioContext) return;

      if (audioContext.state === "suspended") {
         audioContext.resume();
      }

      const midiChannel = 0;
      const midiPort = 0;
      const velocity = 100;
      const currentTime = mainSynth.context.currentTime * 1000;

      const noteOnMessage = [144 + midiChannel, note, velocity];
      const noteOnEvent = new window.RNBO.MIDIEvent(
         currentTime,
         midiPort,
         noteOnMessage,
      );
      mainSynth.scheduleEvent(noteOnEvent);

      const noteOffMessage = [128 + midiChannel, note, 0];
      const noteOffEvent = new window.RNBO.MIDIEvent(
         currentTime + duration,
         midiPort,
         noteOffMessage,
      );
      mainSynth.scheduleEvent(noteOffEvent);
   }

   async function initializeMagenta() {
      try {
         magentaModel = new mm.MusicVAE(
            "https://storage.googleapis.com/magentadata/js/checkpoints/music_vae/trio_4bar",
         );
         await magentaModel.initialize();
         console.log("Magenta model loaded");
      } catch (err) {
         console.error("Magenta failed:", err);
      }
   }

   async function generateMelody(numNotes = 16) {
      if (!magentaModel) {
         console.warn("Model not ready, using random notes");
         return Array(numNotes)
            .fill()
            .map(() => Math.floor(Math.random() * 37) + 48);
      }

      try {
         const samples = await magentaModel.sample(1, 0.7);
         let notes = samples[0].notes.slice(0, numNotes).map((n) => n.pitch);

         const pentatonic = [
            48, 50, 52, 55, 57, 60, 62, 64, 67, 69, 72, 74, 76,
         ];

         notes = notes.map((note) => {
            return pentatonic.reduce((closest, pNote) => {
               return Math.abs(pNote - note) < Math.abs(closest - note)
                  ? pNote
                  : closest;
            }, pentatonic[0]);
         });

         return notes;
      } catch (err) {
         console.error("Generation failed:", err);
         return Array(numNotes)
            .fill()
            .map(() => Math.floor(Math.random() * 37) + 48);
      }
   }

   async function regenerateMelody() {
      const newNotes = await generateMelody(16);
      if (window.p5Instance) {
         window.p5Instance.updateNotes(newNotes);
      }
   }

   function loadP5() {
      const script = document.createElement("script");
      script.src = "/lib/p5.min.js";

      script.onload = () => {
         new window.p5((p) => {
            const canvasSize = 720;
            const rows = 4;
            const cols = 4;
            const ellipseSize = 100;
            let ellipseStates = [];
            let midiNotes = [];

            for (let i = 0; i < rows; i++) {
               ellipseStates[i] = [];
               midiNotes[i] = [];
               for (let j = 0; j < cols; j++) {
                  ellipseStates[i][j] = false;
                  midiNotes[i][j] = 60;
               }
            }

            // Expose functions to window after variables are defined
            window.p5Instance = {
               triggerFromMQTT: (row, col) => {
                  if (row >= 0 && row < rows && col >= 0 && col < cols) {
                     triggerRipple(row, col, !ellipseStates[row][col]);
                  }
               },
               updateNotes: (newNotes) => {
                  let idx = 0;
                  for (let i = 0; i < rows; i++) {
                     for (let j = 0; j < cols; j++) {
                        midiNotes[i][j] = newNotes[idx++] || 60;
                     }
                  }
                  console.log("Notes updated via MQTT");
               },
            };

            generateMelody(rows * cols).then((generatedNotes) => {
               console.log(" Generated notes:", generatedNotes);
               let noteIndex = 0;
               for (let i = 0; i < rows; i++) {
                  for (let j = 0; j < cols; j++) {
                     midiNotes[i][j] = generatedNotes[noteIndex] || 60;
                     noteIndex++;
                  }
               }
               console.log("🎹 Final grid:", midiNotes);
            });

            const xSpacing = canvasSize / (cols + 1);
            const ySpacing = canvasSize / (rows + 1);

            p.setup = () => {
               p.createCanvas(canvasSize, canvasSize);
               p.background(255);
               p.textAlign(p.CENTER, p.CENTER);
            };

            p.draw = () => {
               p.background(255);
               for (let i = 0; i < cols; i++) {
                  for (let j = 0; j < rows; j++) {
                     const x = xSpacing * (i + 1);
                     const y = ySpacing * (j + 1);
                     p.fill(ellipseStates[j][i] ? 0 : 255);
                     p.stroke(0);
                     p.ellipse(x, y, ellipseSize, ellipseSize);
                     p.fill(ellipseStates[j][i] ? 255 : 0);
                     p.textSize(16);
                     p.text(ellipseStates[j][i] ? "ON" : "OFF", x, y - 8);
                     p.textSize(12);
                     p.text(`♪${midiNotes[j][i]}`, x, y + 10);
                  }
               }
            };

            function triggerRipple(clickedRow, clickedCol, newState) {
               const clickedX = xSpacing * (clickedCol + 1);
               const clickedY = ySpacing * (clickedRow + 1);
               let ellipsesWithDistance = [];

               publishState(
                  clickedRow,
                  clickedCol,
                  newState,
                  midiNotes[clickedRow][clickedCol],
               );

               for (let i = 0; i < cols; i++) {
                  for (let j = 0; j < rows; j++) {
                     const x = xSpacing * (i + 1);
                     const y = ySpacing * (j + 1);
                     const distance = p.dist(clickedX, clickedY, x, y);
                     ellipsesWithDistance.push({
                        row: j,
                        col: i,
                        distance: distance,
                        midiNote: midiNotes[j][i],
                     });
                  }
               }

               ellipsesWithDistance.sort((a, b) => a.distance - b.distance);
               ellipsesWithDistance.forEach((ellipse, index) => {
                  setTimeout(() => {
                     ellipseStates[ellipse.row][ellipse.col] = newState;
                     if (newState) {
                        playMIDINote(ellipse.midiNote, 200);
                     }
                  }, index * 300);
               });
            }

            p.mousePressed = () => {
               if (audioContext?.state === "suspended") {
                  audioContext.resume().then(() => {
                     console.log("Audio started");
                  });
               }

               for (let i = 0; i < cols; i++) {
                  for (let j = 0; j < rows; j++) {
                     const x = xSpacing * (i + 1);
                     const y = ySpacing * (j + 1);
                     const distance = p.dist(p.mouseX, p.mouseY, x, y);
                     if (distance < ellipseSize / 2) {
                        triggerRipple(j, i, !ellipseStates[j][i]);
                        return;
                     }
                  }
               }
            };
         });
      };

      document.body.appendChild(script);
   }

   onMount(() => {
      setupRNBO();
      setupMQTT();

      const magentaScript = document.createElement("script");
      magentaScript.src = "/lib/magenta.js";
      magentaScript.onload = async () => {
         console.log("Magenta.js loaded");
         await initializeMagenta();
         console.log("Magenta model ready");
         loadP5();
      };
      document.body.appendChild(magentaScript);
   });

   onDestroy(() => {
      if (mqttClient) {
         mqttClient.end();
         console.log("MQTT disconnected");
      }
   });
</script>

<style>
   :global(html),
   :global(body) {
      margin: 0;
      padding: 0;
      overflow: hidden;
      width: 100vw;
      height: 100vh;
   }

   :global(body) {
      display: flex;
      justify-content: center;
      align-items: center;
      background: #f0f0f0;
   }

   :global(*) {
      scrollbar-width: none;
      -ms-overflow-style: none;
   }

   :global(*::-webkit-scrollbar) {
      display: none;
   }
</style>
