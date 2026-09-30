import React from 'react';
import {createRoot} from 'react-dom/client';
import LiveWorkspace from './LiveWorkspace';
import './styles.css';
createRoot(document.getElementById('root')!).render(<LiveWorkspace onSamples={()=>{}}/>);

