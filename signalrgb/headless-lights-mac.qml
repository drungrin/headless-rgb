Item {
	anchors.fill: parent

	Column {
		width: parent.width
		height: parent.height
		spacing: 10

		Rectangle {
			width: 420
			height: content.childrenRect.height + 20
			color: theme.background3
			radius: theme.radius

			Column {
				id: content
				x: 10
				y: 10
				width: parent.width - 20
				spacing: 6

				Text {
					color: theme.primarytextcolor
					text: "headless-lights Mac bridge"
					font.pixelSize: 16
					font.family: "Poppins"
					font.bold: true
				}

				Text {
					color: theme.secondarytextcolor
					text: "Streams this canvas to the K70 MAX, MM700, G560 and Scimitar attached to the Mac."
					font.pixelSize: 13
					font.family: "Montserrat"
					wrapMode: Text.WordWrap
					width: parent.width
				}

				Text {
					color: theme.secondarytextcolor
					text: "Target: 127.0.0.1 TCP 7532 — the local end of the SSH tunnel."
					font.pixelSize: 13
					font.family: "Montserrat"
					wrapMode: Text.WordWrap
					width: parent.width
				}

				Text {
					color: theme.secondarytextcolor
					text: "The tunnel must be running: windows\\start-mac-tunnel.ps1"
					font.pixelSize: 13
					font.family: "Montserrat"
					wrapMode: Text.WordWrap
					width: parent.width
				}

				Text {
					color: theme.secondarytextcolor
					text: "Stop streaming and the Mac returns to its own effect after 3 seconds."
					font.pixelSize: 13
					font.family: "Montserrat"
					wrapMode: Text.WordWrap
					width: parent.width
				}

				Text {
					color: theme.secondarytextcolor
					text: service.controllers.length + " of 4 devices announced"
					font.pixelSize: 13
					font.family: "Montserrat"
				}
			}
		}
	}
}
