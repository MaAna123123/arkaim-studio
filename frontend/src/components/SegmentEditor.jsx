import ClipComposer from './ClipComposer.jsx';

export default function SegmentEditor(props) {
  return <ClipComposer {...props} sourceDuration={props.duration} />;
}
